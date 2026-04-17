import json
import pickle
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).parent.parent
# Промежуточные артефакты пайплайна
DATA_DIR = BASE_DIR / "data"
MODELS_DIR = BASE_DIR / "models"
OUT_PATH = DATA_DIR / "user_features.parquet"
VOCAB_PATH = MODELS_DIR / "vocab.pkl"

# Директории, где ищем исходные транзакционные файлы (CSV/parquet).
# app/ — рядом с transaction_XXX_new.csv; data/ — альтернатива.
INPUT_SEARCH_DIRS = [BASE_DIR / "app", DATA_DIR]

N_FILES = 5
MIN_TX = 5
TOP_MCC = 50
TOP_TOKENS = 200
MIN_TOK_LEN = 3
IDF_SMOOTH = 1.0

RAW_COLS = [
    "party_rk",
    "category_nm",
    "merchant_type_code",
    "merchant_nm",
    "real_transaction_dttm",
]
TOK_RE = re.compile(r"[\*\-\_\./,|\s]+")

# Значения по умолчанию при пропусках
CATEGORY_UNKNOWN = "unknown"
MCC_UNKNOWN = -1


def _build_from_aggregated(files: list) -> tuple:
    """Строит матрицу из агрегированного user_vectors (category_shares JSON)."""
    frames = []
    for fp in files:
        df = pd.read_parquet(fp) if fp.suffix == ".parquet" else pd.read_csv(fp)
        df["party_rk"] = df["party_rk"].astype(str)
        frames.append(df)

    data = pd.concat(frames, ignore_index=True)
    data = data.drop_duplicates(subset="party_rk")
    data = data[data["total_transactions"] >= MIN_TX]

    all_cats_set: set = set()
    parsed: list = []
    for row in data.itertuples(index=False):
        try:
            shares: dict = (
                json.loads(row.category_shares)
                if isinstance(row.category_shares, str)
                else {}
            )
        except (json.JSONDecodeError, TypeError):
            shares = {}
        parsed.append((str(row.party_rk), int(row.total_transactions), shares))
        all_cats_set.update(shares.keys())

    all_cats = sorted(all_cats_set)

    rows = []
    for rk, n_tx, shares in parsed:
        row: dict = {"party_rk": rk, "tx_count_log": float(np.log1p(n_tx))}
        for cat in all_cats:
            row[f"cat_{cat}"] = float(shares.get(cat, 0.0))
        rows.append(row)

    return rows, all_cats


def _build_from_raw(files: list) -> tuple:
    """Строит матрицу из сырых транзакций (полный набор признаков)."""
    tx_cnt: Counter = Counter()
    cat_cnt: dict = defaultdict(Counter)
    mcc_cnt: dict = defaultdict(Counter)
    hour_cnt: dict = defaultdict(Counter)
    dow_cnt: dict = defaultdict(Counter)
    wknd_cnt: Counter = Counter()
    tok_cnt: dict = defaultdict(Counter)

    for fp in files:
        if fp.suffix == ".parquet":
            df = pd.read_parquet(fp, columns=RAW_COLS)
        else:
            df = pd.read_csv(fp, usecols=lambda c: c in RAW_COLS, low_memory=False)

        # 1. Обработка пропусков (первый шаг пайплайна; одинаково для реальных и моковых данных)
        df["party_rk"] = df["party_rk"].astype(str)
        df["category_nm"] = (
            df["category_nm"]
            .fillna(CATEGORY_UNKNOWN)
            .astype(str)
            .str.strip()
            .replace("", CATEGORY_UNKNOWN)
        )
        df["merchant_nm"] = df["merchant_nm"].fillna("").astype(str).str.lower()
        df["merchant_type_code"] = (
            pd.to_numeric(df["merchant_type_code"], errors="coerce")
            .fillna(MCC_UNKNOWN)
            .astype(int)
        )

        if "real_transaction_dttm" in df.columns:
            df["real_transaction_dttm"] = pd.to_datetime(
                df["real_transaction_dttm"], errors="coerce"
            )
            df["hour"] = df["real_transaction_dttm"].dt.hour
            df["dow"] = df["real_transaction_dttm"].dt.dayofweek
        else:
            df["hour"] = np.nan
            df["dow"] = np.nan

        for row in df.itertuples(index=False):
            rk = row.party_rk
            tx_cnt[rk] += 1
            cat_cnt[rk][row.category_nm] += 1

            mcc = row.merchant_type_code
            if mcc != MCC_UNKNOWN:
                mcc_cnt[rk][int(mcc)] += 1

            h = row.hour
            if h is not None and not (isinstance(h, float) and np.isnan(h)):
                hour_cnt[rk][int(h)] += 1

            d = row.dow
            if d is not None and not (isinstance(d, float) and np.isnan(d)):
                d_int = int(d)
                dow_cnt[rk][d_int] += 1
                if d_int in (5, 6):
                    wknd_cnt[rk] += 1

            nm = row.merchant_nm or ""
            toks = [
                t for t in TOK_RE.split(nm) if len(t) >= MIN_TOK_LEN and not t.isdigit()
            ]
            tok_cnt[rk].update(toks)

        del df

    active = {rk for rk, n in tx_cnt.items() if n >= MIN_TX}

    all_cats = sorted({cat for rk in active for cat in cat_cnt[rk]})

    global_mcc: Counter = Counter()
    for rk in active:
        global_mcc.update(mcc_cnt[rk])
    top_mcc = [mcc for mcc, _ in global_mcc.most_common(TOP_MCC)]

    tok_doc_cnt: Counter = Counter()
    for rk in active:
        tok_doc_cnt.update(set(tok_cnt[rk].keys()))
    top_tokens = [tok for tok, _ in tok_doc_cnt.most_common(TOP_TOKENS)]

    n_users = len(active)
    idf = {
        tok: float(np.log((n_users + IDF_SMOOTH) / (tok_doc_cnt[tok] + IDF_SMOOTH)))
        for tok in top_tokens
    }

    top_mcc_set = set(top_mcc)
    rows = []
    for rk in active:
        n = tx_cnt[rk]
        row: dict = {"party_rk": rk, "tx_count_log": float(np.log1p(n))}

        total_cat = max(sum(cat_cnt[rk].values()), 1)
        for cat in all_cats:
            row[f"cat_{cat}"] = cat_cnt[rk].get(cat, 0) / total_cat

        total_mcc = max(sum(mcc_cnt[rk].values()), 1)
        for mcc in top_mcc:
            row[f"mcc_{mcc}"] = mcc_cnt[rk].get(mcc, 0) / total_mcc
        row["mcc_other"] = (
            sum(v for k, v in mcc_cnt[rk].items() if k not in top_mcc_set) / total_mcc
        )

        total_h = max(sum(hour_cnt[rk].values()), 1)
        for h in range(24):
            row[f"hour_{h}"] = hour_cnt[rk].get(h, 0) / total_h

        total_d = max(sum(dow_cnt[rk].values()), 1)
        for d in range(7):
            row[f"dow_{d}"] = dow_cnt[rk].get(d, 0) / total_d

        row["weekend_ratio"] = wknd_cnt[rk] / n
        row["merchant_entropy"] = float(np.log1p(len(mcc_cnt[rk])))

        total_toks = max(sum(tok_cnt[rk].values()), 1)
        for tok in top_tokens:
            tf = tok_cnt[rk].get(tok, 0) / total_toks
            row[f"tok_{tok}"] = tf * idf.get(tok, 0.0)

        rows.append(row)

    return rows, all_cats, top_mcc, top_tokens, idf


def main() -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Ищем входные файлы: CSV из app/ имеют приоритет (реальные сырые транзакции),
    # затем parquet из data/ (агрегированные).
    app_csvs = sorted((BASE_DIR / "app").glob("transaction_*.csv"))
    data_parquets = [
        f
        for f in sorted(DATA_DIR.glob("*.parquet"))
        if f.stem not in ("user_features", "user_clusters")
    ]
    # Также учитываем CSV в data/ и parquet в app/
    other_csvs = [f for f in sorted(DATA_DIR.glob("*.csv"))]
    other_parquets = [
        f
        for f in sorted((BASE_DIR / "app").glob("*.parquet"))
        if f.stem not in ("user_features", "user_clusters")
    ]

    files = (app_csvs or other_csvs or data_parquets or other_parquets)[:N_FILES]

    if not files:
        print(
            f"ERROR: не найдены входные файлы.\n"
            f"  Ожидаемые пути: {BASE_DIR / 'app'}/transaction_*.csv\n"
            f"               или {DATA_DIR}/*.parquet",
            file=sys.stderr,
        )
        sys.exit(1)

    first = files[0]
    probe = (
        pd.read_parquet(first, columns=None).columns.tolist()
        if first.suffix == ".parquet"
        else pd.read_csv(first, nrows=0).columns.tolist()
    )
    is_aggregated = "category_shares" in probe and "category_nm" not in probe

    if is_aggregated:
        rows, all_cats = _build_from_aggregated(files)
        top_mcc: list = []
        top_tokens: list = []
        idf: dict = {}
    else:
        rows, all_cats, top_mcc, top_tokens, idf = _build_from_raw(files)

    df_feat = pd.DataFrame(rows).set_index("party_rk")

    vocab = {
        "all_cats": all_cats,
        "top_mcc": top_mcc,
        "top_tokens": top_tokens,
        "feature_columns": df_feat.columns.tolist(),
        "idf": idf,
    }

    df_feat.to_parquet(OUT_PATH)
    with open(VOCAB_PATH, "wb") as fh:
        pickle.dump(vocab, fh)


if __name__ == "__main__":
    main()
