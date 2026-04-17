from __future__ import annotations

import logging
import os
from datetime import date

import redis.asyncio as aioredis
from sqlalchemy import cast, select
from sqlalchemy import String as SAString
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.filters import MIN_CITY_RESULTS, city_conditions, strict_conditions
from app.model.baseline import MLRecommender
from app.models.interaction import Interaction
from app.models.user import User

DAILY_RECOMMENDATIONS_LIMIT = 20

_recommender = MLRecommender()
_REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
_redis: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(_REDIS_URL, decode_responses=True)
    return _redis


def _make_error(code: str, message: str) -> dict:
    return {"data": None, "error": {"code": code, "message": message}}


async def get_daily_count(user_id: int) -> int:
    key = f"rec:daily:{user_id}:{date.today().isoformat()}"
    try:
        val = await _get_redis().get(key)
        return int(val) if val else 0
    except Exception:
        return 0


async def _incr_daily_count(user_id: int, count: int) -> None:
    key = f"rec:daily:{user_id}:{date.today().isoformat()}"
    try:
        pipe = _get_redis().pipeline()
        await pipe.incrby(key, count)
        await pipe.expire(key, 86400)
        await pipe.execute()
    except Exception:
        pass


async def _query_candidates(
    session: AsyncSession,
    recommended_rks: list[str],
    user_id: int,
    extra_conditions: list,
) -> dict[str, int]:
    stmt = select(User.id, User.external_party_rk).where(
        cast(User.external_party_rk, SAString).in_(recommended_rks),
        User.is_active.is_(True),
        User.id != user_id,
        *extra_conditions,
    )
    res = await session.execute(stmt)
    return {str(row.external_party_rk): row.id for row in res.all()}


async def get_recommendations(session: AsyncSession, user_id: int, top_k: int) -> dict:
    return await _get_recommendations(
        session=session,
        user_id=user_id,
        top_k=top_k,
        recommender=_recommender,
        get_daily_count_fn=get_daily_count,
    )


async def _get_recommendations(
    session: AsyncSession,
    user_id: int,
    top_k: int,
    *,
    recommender: MLRecommender,
    get_daily_count_fn,
    strict_conditions_fn=strict_conditions,
    city_conditions_fn=city_conditions,
    min_city_results: int = MIN_CITY_RESULTS,
) -> dict:
    daily_count = await get_daily_count_fn(user_id)
    if daily_count >= DAILY_RECOMMENDATIONS_LIMIT:
        return _make_error(
            "DAILY_LIMIT_REACHED",
            f"Лимит рекомендаций на сегодня исчерпан ({DAILY_RECOMMENDATIONS_LIMIT})",
        )

    user_res = await session.execute(
        select(User).where(User.id == user_id, User.is_active.is_(True))
    )
    target_user = user_res.scalar_one_or_none()
    if not target_user:
        return _make_error("USER_NOT_FOUND", "User not found")

    party_rk = str(target_user.external_party_rk or target_user.id)

    try:
        scored_rks = await recommender.get_recommendations_scored(
            session, party_rk=party_rk, top_k=top_k
        )
    except Exception:
        scored_rks = []

    recommended_rks = [rk for rk, _ in scored_rks]
    scores_map: dict[str, float] = {rk: score for rk, score in scored_rks}

    seen_res = await session.execute(
        select(Interaction.target_id).where(Interaction.actor_id == user_id)
    )
    seen_ids = {row[0] for row in seen_res.all()}

    base_conds = strict_conditions_fn(target_user)

    rk_to_uid: dict[str, int] = {}
    if recommended_rks:
        rk_to_uid = await _query_candidates(
            session,
            recommended_rks,
            user_id,
            base_conds + city_conditions_fn(target_user),
        )
        if len(rk_to_uid) < min_city_results:
            rk_to_uid = await _query_candidates(
                session, recommended_rks, user_id, base_conds
            )

    final: list[tuple[int, str, float]] = []
    for rk in recommended_rks:
        uid = rk_to_uid.get(rk)
        if uid is None or uid in seen_ids:
            continue
        final.append((uid, rk, scores_map.get(rk, 0.0)))
        if len(final) >= top_k:
            break

    if not final:
        logging.warning(
            "Recommendations pool empty for user_id=%s, using fallback",
            user_id,
        )
        fallback_res = await session.execute(
            select(User.id)
            .where(User.is_active.is_(True), User.id != user_id)
            .where(*base_conds)
            .order_by(User.id)
            .limit(top_k)
        )
        result = [
            {
                "user_id": row[0],
                "match_reason": "вам может понравиться этот пользователь",
            }
            for row in fallback_res.all()
        ]
        return {"data": result, "error": None}

    result = []
    for i, (uid, rk, score) in enumerate(final):
        if i == 0:
            reason = await recommender.explain_match_async(party_rk, rk, score)
        else:
            reason = recommender.explain_match(party_rk, rk, score)
        result.append({"user_id": uid, "match_reason": reason})

    await _incr_daily_count(user_id, len(result))
    return {"data": result, "error": None}
