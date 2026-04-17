from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.filters import MIN_CITY_RESULTS, city_conditions, strict_conditions
from app.db.database import get_session
from app.model.baseline import MLRecommender
from app.models.interaction import Interaction
from app.models.user import User

router = APIRouter()

SKIP_COOLDOWN_DAYS = 30

_recommender = MLRecommender()


def _make_error(code: str, message: str) -> dict:
    return {"data": None, "error": {"code": code, "message": message}}


def _serialize_user_card(user: User) -> dict:
    primary_photo = None
    if user.photos:
        primary_photo = next((p for p in user.photos if p.is_primary), user.photos[0])
    return {
        "user_id": user.id,
        "name": f"{user.first_name} {user.last_name}",
        "age": _calc_age(user.birth_date),
        "photo_url": primary_photo.url if primary_photo else None,
    }


def _calc_age(birth_date: date | None) -> int | None:
    if not birth_date:
        return None
    today = datetime.utcnow().date()
    years = today.year - birth_date.year
    if (today.month, today.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years


async def _load_exclusions(
    session: AsyncSession, user_id: int, cooldown_days: int
) -> set[int]:
    cutoff = datetime.utcnow() - timedelta(days=cooldown_days)

    skip_stmt = select(Interaction.target_id).where(
        Interaction.actor_id == user_id,
        Interaction.action == "skip",
        Interaction.created_at >= cutoff,
    )
    hide_stmt = select(Interaction.target_id).where(
        Interaction.actor_id == user_id,
        Interaction.action == "hide",
    )
    hide_reverse_stmt = select(Interaction.actor_id).where(
        Interaction.target_id == user_id,
        Interaction.action == "hide",
    )

    skip_ids = set((await session.execute(skip_stmt)).scalars().all())
    hide_ids = set((await session.execute(hide_stmt)).scalars().all())
    hide_reverse_ids = set((await session.execute(hide_reverse_stmt)).scalars().all())
    return skip_ids.union(hide_ids).union(hide_reverse_ids)


async def _fetch_users_map(
    session: AsyncSession,
    user_ids: set[int],
    party_ids: set[int],
    extra_conditions: list | None = None,
) -> dict[int, User]:
    if not user_ids and not party_ids:
        return {}

    stmt = (
        select(User)
        .options(selectinload(User.photos))
        .where(User.is_active.is_(True))
        .where(or_(User.id.in_(user_ids), User.external_party_rk.in_(party_ids)))
    )
    if extra_conditions:
        stmt = stmt.where(*extra_conditions)

    result = await session.execute(stmt)
    users = result.scalars().all()
    mapping: dict[int, User] = {}
    for user in users:
        mapping[user.id] = user
        if user.external_party_rk is not None:
            mapping[int(user.external_party_rk)] = user
    return mapping


def _normalize_party_id(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _build_rank_payload(
    session: AsyncSession,
    user_id: int,
    recs: list[dict],
    limit: int,
    cooldown_days: int,
    extra_conditions: list | None = None,
) -> list[dict]:
    excluded_ids = await _load_exclusions(session, user_id, cooldown_days)

    user_ids: set[int] = set()
    party_ids: set[int] = set()
    for rec in recs:
        rec_user_id = rec.get("user_id")
        if isinstance(rec_user_id, int):
            user_ids.add(rec_user_id)
        else:
            party_id = _normalize_party_id(rec.get("party_rk"))
            if party_id is not None:
                party_ids.add(party_id)

    users_map = await _fetch_users_map(
        session, user_ids, party_ids, extra_conditions=extra_conditions
    )

    payload = []
    for rec in recs:
        rec_user_id = rec.get("user_id")
        party_id = _normalize_party_id(rec.get("party_rk"))
        user = None
        if isinstance(rec_user_id, int):
            user = users_map.get(rec_user_id)
        if user is None and party_id is not None:
            user = users_map.get(party_id)
        if not user:
            continue
        if user.id == user_id or user.id in excluded_ids:
            continue

        payload.append(
            {
                **_serialize_user_card(user),
                "party_rk": rec.get("party_rk"),
                "score": rec.get("score"),
                "match_reason": rec.get("match_reason"),
            }
        )
        if len(payload) >= limit:
            break
    return payload


@router.get("/rank")
async def get_rank(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return _make_error("USER_ID_REQUIRED", "X-User-ID header required")

    user_stmt = select(User).where(User.id == user_id)
    target_user = (await session.execute(user_stmt)).scalar_one_or_none()
    if not target_user:
        return _make_error("USER_NOT_FOUND", "User not found")

    party_rk = str(target_user.external_party_rk or target_user.id)
    scored_rks = await _recommender.get_recommendations_scored(
        session=session, party_rk=party_rk, top_k=limit
    )

    # Рекомендации без объяснений — объяснения генерируем только для финального списка.
    scores_map: dict[str, float] = {rk: score for rk, score in scored_rks}
    recs = [{"party_rk": rk} for rk, _ in scored_rks]

    base_conds = strict_conditions(target_user)

    # Сначала пробуем с фильтром города.
    payload = await _build_rank_payload(
        session=session,
        user_id=user_id,
        recs=recs,
        limit=limit,
        cooldown_days=SKIP_COOLDOWN_DAYS,
        extra_conditions=base_conds + city_conditions(target_user),
    )

    # Если в городе мало кандидатов — повторяем без фильтра города.
    if len(payload) < MIN_CITY_RESULTS:
        payload = await _build_rank_payload(
            session=session,
            user_id=user_id,
            recs=recs,
            limit=limit,
            cooldown_days=SKIP_COOLDOWN_DAYS,
            extra_conditions=base_conds,
        )

    # Если ML совсем ничего не нашёл — случайные активные пользователи с фильтрами.
    if not payload:
        fallback_stmt = (
            select(User)
            .options(selectinload(User.photos))
            .where(User.is_active.is_(True), User.id != user_id)
            .where(*base_conds)
            .order_by(User.id)
            .limit(limit)
        )
        fallback_users = (await session.execute(fallback_stmt)).scalars().all()
        payload = [_serialize_user_card(u) for u in fallback_users]

    # Для топ-1 мэтча — LLM (кэшируется в Redis на 7 дней).
    # Для остальных — SHAP-объяснение (мгновенно, офлайн).
    enriched = []
    for i, item in enumerate(payload):
        rk = item.get("party_rk") or ""
        score = scores_map.get(str(rk), 0.0)
        if i == 0:
            reason = await _recommender.explain_match_async(party_rk, str(rk), score)
        else:
            reason = _recommender.explain_match(party_rk, str(rk), score)
        enriched.append({**item, "score": score, "match_reason": reason})
    payload = enriched

    return {"data": payload, "error": None}
