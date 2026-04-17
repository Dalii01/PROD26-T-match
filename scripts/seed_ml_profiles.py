import asyncio
import json
import os
import sys
from collections import defaultdict

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

DB_URL = os.getenv("DB_URL", "postgresql+asyncpg://postgres:postgres@db:5432/tmatch")


async def main() -> None:
    engine = create_async_engine(DB_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # 1. Считаем category_shares из transactions для каждого party_rk
        print("Читаем транзакции из БД...")
        rows = await session.execute(
            text(
                """
            SELECT
                party_rk,
                COALESCE(NULLIF(TRIM(category_nm), ''), 'Другое') AS category_nm,
                COUNT(*) AS cnt
            FROM transactions
            GROUP BY party_rk, category_nm
        """
            )
        )

        cat_counts: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        totals: dict[int, int] = defaultdict(int)

        for row in rows.mappings():
            rk = row["party_rk"]
            cat = row["category_nm"]
            cnt = row["cnt"]
            cat_counts[rk][cat] += cnt
            totals[rk] += cnt

        print(f"Найдено {len(totals)} пользователей с транзакциями.")

        # 2. Нормализуем → category_shares
        profiles = []
        for rk, total in totals.items():
            shares = {cat: round(cnt / total, 4) for cat, cnt in cat_counts[rk].items()}
            profiles.append(
                {
                    "party_rk": str(
                        rk
                    ),  # BaselineRecommender ищет по str(external_party_rk)
                    "total_transactions": int(total),
                    "category_shares": json.dumps(shares, ensure_ascii=False),
                }
            )

        print(
            f"Подготовлено {len(profiles)} ML-профилей. Загружаем в user_ml_profiles..."
        )

        # 3. Upsert в user_ml_profiles
        upsert_sql = text(
            """
            INSERT INTO user_ml_profiles (party_rk, total_transactions, category_shares, updated_at)
            VALUES (:party_rk, :total_transactions, :category_shares, NOW())
            ON CONFLICT (party_rk) DO UPDATE SET
                total_transactions = EXCLUDED.total_transactions,
                category_shares    = EXCLUDED.category_shares,
                updated_at         = NOW()
        """
        )

        batch_size = 500
        for i in range(0, len(profiles), batch_size):
            batch = profiles[i : i + batch_size]
            await session.execute(upsert_sql, batch)

        await session.commit()
        print("Готово! user_ml_profiles обновлены.")

        # 4. Проверка — party_rk моковых пользователей числовые (10000000+)
        check = await session.execute(
            text(
                """
            SELECT COUNT(*) AS cnt FROM user_ml_profiles
            WHERE party_rk ~ '^[0-9]+$'
              AND party_rk::bigint >= 10000000
        """
            )
        )
        cnt = check.scalar()
        print(f"Записей для моковых пользователей в user_ml_profiles: {cnt}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
