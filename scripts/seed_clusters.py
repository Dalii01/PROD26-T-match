"""
Встраивает моковых пользователей из БД в PCA-пространство модели.

Запускается ОДИН РАЗ после деплоя (или локально для теста).
Не переобучает модель — только применяет сохранённые веса.

Шаги:
  1. Читает транзакции из таблицы transactions в БД.
  2. Считает те же 221 фичу (по сохранённому vocab.pkl).
  3. Применяет scaler.pkl + pca.pkl.
  4. Обновляет data/user_clusters.parquet.

Запуск на сервере:
    docker compose exec app python /app/scripts/seed_clusters.py
Локально:
    python scripts/seed_clusters.py
"""

import asyncio
import json
import os
import pickle
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

load_dotenv()

BASE_DIR = Path(__file__).parent.parent
MODELS_DIR = BASE_DIR / "models"
CLUSTERS_PATH = MODELS_DIR / "user_clusters.parquet"

DB_URL = os.getenv("DB_URL", "postgresql+asyncpg://postgres:postgres@db:5432/tmatch")

MIN_TX = 5
MIN_TOK = 3
TOK_RE = re.compile(r"[\*\-\_\./,|\s]+")


def section(msg: str) -> None:
    print(f"\n{'=' * 55}\n  {msg}\n{'=' * 55}", flush=True)


def build_feature_row(
    party_rk: str,
    tx_count: int,
    cat_cnt: Counter,
    mcc_cnt: Counter,
    hour_cnt: Counter,
    dow_cnt: Counter,
    wknd_count: int,
    tok_cnt: Counter,
    vocab: dict,
    idf: dict,
) -> dict:
    n = tx_count
    row: dict = {"party_rk": party_rk, "tx_count_log": float(np.log1p(n))}

    total_cat = max(sum(cat_cnt.values()), 1)
    for cat in vocab["all_cats"]:
        row[f"cat_{cat}"] = cat_cnt.get(cat, 0) / total_cat

    top_mcc_s = set(vocab["top_mcc"])
    total_mcc = max(sum(mcc_cnt.values()), 1)
    for mcc in vocab["top_mcc"]:
        row[f"mcc_{mcc}"] = mcc_cnt.get(mcc, 0) / total_mcc
    row["mcc_other"] = (
        sum(v for k, v in mcc_cnt.items() if k not in top_mcc_s) / total_mcc
    )

    total_h = max(sum(hour_cnt.values()), 1)
    for h in range(24):
        row[f"hour_{h}"] = hour_cnt.get(h, 0) / total_h

    total_d = max(sum(dow_cnt.values()), 1)
    for d in range(7):
        row[f"dow_{d}"] = dow_cnt.get(d, 0) / total_d

    row["weekend_ratio"] = wknd_count / n
    row["merchant_entropy"] = float(np.log1p(len(mcc_cnt)))

    total_toks = max(sum(tok_cnt.values()), 1)
    for tok in vocab["top_tokens"]:
        tf = tok_cnt.get(tok, 0) / total_toks
        row[f"tok_{tok}"] = tf * idf.get(tok, 0.0)

    return row


async def main() -> None:
    section("Загружаем модель...")

    for fname in ("vocab.pkl", "scaler.pkl", "pca.pkl", "kmeans.pkl"):
        if not (MODELS_DIR / fname).exists():
            print(f"ERROR: {MODELS_DIR / fname} не найден.")
            print(
                "Запустите сначала:\n"
                "  python scripts/build_features.py\n"
                "  python scripts/train_pca_kmeans.py"
            )
            sys.exit(1)

    with open(MODELS_DIR / "vocab.pkl", "rb") as fh:
        vocab: dict = pickle.load(fh)
    with open(MODELS_DIR / "scaler.pkl", "rb") as fh:
        scaler = pickle.load(fh)
    with open(MODELS_DIR / "pca.pkl", "rb") as fh:
        pca = pickle.load(fh)
    with open(MODELS_DIR / "kmeans.pkl", "rb") as fh:
        kmeans = pickle.load(fh)

    feature_cols = vocab["feature_columns"]
    tok_vocab_set = set(vocab["top_tokens"])
    n_existing = len(pd.read_parquet(CLUSTERS_PATH)) if CLUSTERS_PATH.exists() else 0
    idf = {tok: float(np.log((n_existing + 1) / 1)) for tok in vocab["top_tokens"]}

    print(
        f"Фичей: {len(feature_cols)} | PCA компонентов: {pca.n_components_}", flush=True
    )

    section("Читаем транзакции из БД...")

    engine = create_async_engine(DB_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        result = await session.execute(
            text(
                """
                SELECT
                    party_rk::text                                          AS party_rk,
                    COALESCE(NULLIF(TRIM(category_nm), ''), 'unknown')      AS category_nm,
                    COALESCE(merchant_type_code, -1)::int                  AS merchant_type_code,
                    LOWER(COALESCE(merchant_nm, ''))                        AS merchant_nm,
                    EXTRACT(HOUR FROM real_transaction_dttm)::int           AS hour,
                    EXTRACT(DOW  FROM real_transaction_dttm)::int           AS dow
                FROM transactions
                WHERE party_rk IS NOT NULL
                """
            )
        )
        data = result.mappings().all()

    await engine.dispose()
    print(f"Транзакций в БД: {len(data):,}", flush=True)

    section("Считаем фичи...")

    tx_cnt: Counter = Counter()
    cat_cnt: dict[str, Counter] = defaultdict(Counter)
    mcc_cnt: dict[str, Counter] = defaultdict(Counter)
    hour_cnt: dict[str, Counter] = defaultdict(Counter)
    dow_cnt: dict[str, Counter] = defaultdict(Counter)
    wknd_cnt: Counter = Counter()
    tok_cnt: dict[str, Counter] = defaultdict(Counter)

    for row in data:
        rk = str(row["party_rk"])
        tx_cnt[rk] += 1
        cat_cnt[rk][row["category_nm"]] += 1

        mcc_raw = row["merchant_type_code"]
        if mcc_raw is not None and mcc_raw != -1:
            mcc_cnt[rk][int(mcc_raw)] += 1

        h = row["hour"]
        d = row["dow"]
        if h is not None:
            hour_cnt[rk][int(h)] += 1
        if d is not None:
            d_int = int(d)
            dow_cnt[rk][d_int] += 1
            if d_int in (0, 6):  # воскресенье=0, суббота=6 в PostgreSQL DOW
                wknd_cnt[rk] += 1

        nm = row["merchant_nm"] or ""
        toks = [t for t in TOK_RE.split(nm) if len(t) >= MIN_TOK and not t.isdigit()]
        tok_cnt[rk].update(t for t in toks if t in tok_vocab_set)

    active = {rk for rk, n in tx_cnt.items() if n >= MIN_TX}
    print(f"Пользователей >= {MIN_TX} транзакций: {len(active):,}", flush=True)

    section("Применяем scaler + PCA...")

    rows_out = []
    for rk in active:
        rows_out.append(
            build_feature_row(
                party_rk=rk,
                tx_count=tx_cnt[rk],
                cat_cnt=cat_cnt[rk],
                mcc_cnt=mcc_cnt[rk],
                hour_cnt=hour_cnt[rk],
                dow_cnt=dow_cnt[rk],
                wknd_count=wknd_cnt[rk],
                tok_cnt=tok_cnt[rk],
                vocab=vocab,
                idf=idf,
            )
        )

    df_new = pd.DataFrame(rows_out).set_index("party_rk")
    df_new = df_new.reindex(columns=feature_cols, fill_value=0.0).fillna(0.0)

    x_sc = scaler.transform(df_new.values.astype(np.float32))
    x_pca = pca.transform(x_sc).astype(np.float32)

    pca_cols = [f"pca_{i}" for i in range(pca.n_components_)]
    df_result = pd.DataFrame(x_pca, columns=pca_cols, index=df_new.index)
    df_result["cluster"] = kmeans.predict(x_pca).astype(np.int32)
    df_result.index.name = "party_rk"
    print(
        f"Кластеров назначено: {df_result['cluster'].nunique()} "
        f"(ожидается {kmeans.n_clusters})",
        flush=True,
    )

    # top_cats — топ-3 категории для объяснений мэтчей (аналог train_pca_kmeans.py)
    top_cats_map = {}
    for rk in active:
        total = max(sum(cat_cnt[rk].values()), 1)
        shares = {cat: cnt / total for cat, cnt in cat_cnt[rk].items()}
        significant = sorted(
            ((cat, v) for cat, v in shares.items() if v > 0.05),
            key=lambda x: x[1],
            reverse=True,
        )[:3]
        top_cats_map[rk] = json.dumps(
            [cat for cat, _ in significant], ensure_ascii=False
        )

    df_result["top_cats"] = [top_cats_map.get(rk, "[]") for rk in df_result.index]

    section("Обновляем user_clusters.parquet...")

    if CLUSTERS_PATH.exists():
        df_existing = pd.read_parquet(CLUSTERS_PATH)
        df_existing = df_existing[~df_existing.index.isin(df_result.index)]
        df_combined = pd.concat([df_existing, df_result])
    else:
        df_combined = df_result

    df_combined.to_parquet(CLUSTERS_PATH)
    size_mb = CLUSTERS_PATH.stat().st_size / 1e6

    section("Готово!")
    print(f"  Добавлено/обновлено:           {len(df_result):,}")
    print(f"  Всего в user_clusters.parquet: {len(df_combined):,}")
    print(f"  Размер файла: {size_mb:.0f} МБ")


if __name__ == "__main__":
    asyncio.run(main())
