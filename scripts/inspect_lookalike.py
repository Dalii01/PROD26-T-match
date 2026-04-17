import asyncio
import os
import json
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from app.model.baseline import BaselineRecommender
from app.model.ml_profile import UserMLProfile
from dotenv import load_dotenv

load_dotenv()


async def inspect_lookalike():
    db_url = os.getenv("DB_URL")
    if not db_url:
        print("DB_URL not found.")
        return

    engine = create_async_engine(db_url)
    AsyncSessionLocal = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with AsyncSessionLocal() as session:
        # 1. Get a sample user
        result = await session.execute(select(UserMLProfile).limit(1))
        target_user = result.scalar_one_or_none()

        if not target_user:
            print("No users found. Run ETL scripts first.")
            return

        print(f"--- TARGET USER PROFILE ---")
        print(f"Party RK: {target_user.party_rk}")
        print(f"Transactions: {target_user.total_transactions}")
        print(
            f"Top Categories: {json.dumps(target_user.category_shares, indent=2, ensure_ascii=False)}"
        )

        # 2. Get recommendations
        recommender = BaselineRecommender()
        recommendations = await recommender.get_recommendations(
            session, target_user.party_rk, top_k=5
        )

        print(f"\n--- TOP 5 LOOK-ALIKE MATCHES ---")
        for i, rec in enumerate(recommendations, 1):
            # Fetch full profile for the match
            res = await session.execute(
                select(UserMLProfile).where(UserMLProfile.party_rk == rec["party_rk"])
            )
            match_profile = res.scalar_one()

            print(f"\n{i}. Recommendation (Score: {rec['score']:.4f})")
            print(f"   Party RK: {match_profile.party_rk}")
            print(f"   Transactions: {match_profile.total_transactions}")
            print(
                f"   Top Categories: {json.dumps(match_profile.category_shares, indent=2, ensure_ascii=False)}"
            )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(inspect_lookalike())
