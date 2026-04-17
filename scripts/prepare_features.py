import glob
import json
import os
from collections import defaultdict
from pathlib import Path

import pandas as pd


def prepare_features(data_dir="data", output_file="data/user_vectors.parquet"):
    print(f"Starting feature preparation from {data_dir}...")
    files = glob.glob(os.path.join(data_dir, "transaction_*_new.csv"))
    files = sorted(files)

    if not files:
        print(f"No transaction files found in {data_dir}")
        return

    user_category_counts = defaultdict(lambda: defaultdict(int))
    user_total_transactions = defaultdict(int)

    total_files = len(files)
    for i, file_path in enumerate(files):
        print(f"[{i+1}/{total_files}] Processing {file_path}...")

        try:
            chunks = pd.read_csv(
                file_path,
                usecols=["party_rk", "category_nm"],
                chunksize=100000,
                on_bad_lines="skip",
            )

            for chunk in chunks:
                chunk["category_nm"] = (
                    chunk["category_nm"]
                    .fillna("unknown")
                    .astype(str)
                    .str.strip()
                    .replace("", "unknown")
                )

                counts = chunk["party_rk"].value_counts()
                for party_rk, count in counts.items():
                    user_total_transactions[party_rk] += count

                cat_counts = (
                    chunk.groupby(["party_rk", "category_nm"])
                    .size()
                    .reset_index(name="count")
                )
                for _, row in cat_counts.iterrows():
                    user_category_counts[row["party_rk"]][row["category_nm"]] += row[
                        "count"
                    ]

        except Exception as e:
            print(f"Error processing {file_path}: {e}")

    print("Aggregation complete. Calculating shares...")

    # Transform to final format (compatible with JSONB category_shares)
    data = []
    for party_rk, total in user_total_transactions.items():
        shares = {}
        counts = user_category_counts[party_rk]
        for cat, count in counts.items():
            shares[cat] = round(count / (total if total > 0 else 1), 4)

        data.append(
            {
                "party_rk": str(party_rk),
                "total_transactions": int(total),
                "category_shares": json.dumps(shares, ensure_ascii=False),
            }
        )

    df_final = pd.DataFrame(data)

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    print(f"Saving {len(df_final)} user vectors to {output_file}...")
    df_final.to_parquet(output_file, index=False, compression="snappy")
    print("Done!")


def run_pipeline(raw_transactions_path: str, output_parquet_path: str):
    """Wrapper to match alternate naming convention if needed."""
    prepare_features(
        data_dir=os.path.dirname(raw_transactions_path), output_file=output_parquet_path
    )


if __name__ == "__main__":
    Path("data").mkdir(exist_ok=True)
    prepare_features()
