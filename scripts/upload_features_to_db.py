import asyncio
import json
import os

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

load_dotenv()


async def upload_features(parquet_path="data/user_vectors.parquet"):

    if not os.path.exists(parquet_path):
        print(f"File {parquet_path} not found. Run prepare_features.py first.")
        return

    print(f"Loading features from {parquet_path}...")
    df = pd.read_parquet(parquet_path)
    print(f"Loaded {len(df)} user vectors.")

    db_url = os.getenv("DB_URL")
    if not db_url:
        print("DB_URL not found in environment variables.")
        return

    engine = create_async_engine(db_url)

    print("Uploading to database...")
    async with engine.begin() as conn:
        batch_size = 5000
        for i in range(0, len(df), batch_size):
            batch = df.iloc[i : i + batch_size]

            sql = text(
                """
                INSERT INTO user_ml_profiles (party_rk, total_transactions, category_shares, updated_at)
                VALUES (:party_rk, :total_transactions, :category_shares, NOW())
                ON CONFLICT (party_rk) DO UPDATE SET
                    total_transactions = EXCLUDED.total_transactions,
                    category_shares = EXCLUDED.category_shares,
                    updated_at = NOW()
            """
            )

            params = []
            for _, row in batch.iterrows():
                params.append(
                    {
                        "party_rk": str(row["party_rk"]),
                        "total_transactions": int(row["total_transactions"]),
                        "category_shares": row["category_shares"],
                    }
                )

            await conn.execute(sql, params)
            print(
                f"Uploaded batch {i // batch_size + 1}/{(len(df)-1) // batch_size + 1}..."
            )

    print("Upload complete!")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(upload_features())
