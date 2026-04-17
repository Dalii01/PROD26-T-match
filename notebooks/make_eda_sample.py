"""
Создаёт сэмпл транзакций для EDA (eda.ipynb) из реальных данных.
Данные в data/ в .gitignore — на сервер не попадают; сэмпл в этой же папке (notebooks/)
можно закоммитить, чтобы проверяющие могли запустить EDA.

Ищет transaction_*.csv в: data/ → app/ → корень проекта.
Берёт до SAMPLE_ROWS строк (по умолчанию 100k) из одного или нескольких файлов.
Сохраняет в notebooks/sample_data/sample_transactions.csv.

Запуск из корня проекта:
  python notebooks/make_eda_sample.py
  python notebooks/make_eda_sample.py --rows 500000
"""

import argparse
from pathlib import Path

import pandas as pd

# Корень проекта (для поиска data/, app/)
ROOT = Path(__file__).resolve().parent.parent
# Сэмпл — рядом с ноутбуком
OUT_DIR = Path(__file__).resolve().parent / "sample_data"
OUT_FILE = OUT_DIR / "sample_transactions.csv"

SEARCH_DIRS = [
    ROOT / "data",
    ROOT / "app",
    ROOT,
]
GLOB = "transaction_*.csv"
SAMPLE_ROWS = 100_000


def main():
    parser = argparse.ArgumentParser(description="Создать сэмпл транзакций для EDA")
    parser.add_argument(
        "--rows",
        type=int,
        default=SAMPLE_ROWS,
        help=f"Максимум строк в сэмпле (по умолчанию {SAMPLE_ROWS})",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=5,
        help="Максимум файлов, из которых брать строки",
    )
    args = parser.parse_args()

    files = []
    for d in SEARCH_DIRS:
        if not d.exists():
            continue
        found = sorted(d.glob(GLOB))[: args.max_files]
        if found:
            files = found
            break

    if not files:
        print(
            f"Не найдено ни одного файла {GLOB} в:\n  "
            + "\n  ".join(str(d) for d in SEARCH_DIRS)
        )
        print(
            "\nПоложи 1–5 CSV с транзакциями (колонки: party_rk, category_nm, "
            "merchant_type_code, merchant_nm, real_transaction_dttm) в data/ или app/"
        )
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows_per_file = max(1, args.rows // len(files))
    chunks = []
    total = 0
    for fp in files:
        if total >= args.rows:
            break
        need = min(rows_per_file, args.rows - total)
        df = pd.read_csv(fp, nrows=need + 1)
        df = df.head(need)
        chunks.append(df)
        total += len(df)

    out = pd.concat(chunks, ignore_index=True)
    out.to_csv(OUT_FILE, index=False)
    print(f"Сохранено {len(out):,} строк в {OUT_FILE}")
    print(f"Источники: {[f.name for f in files]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
