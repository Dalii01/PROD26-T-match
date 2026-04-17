from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import add_audit_log
from app.models.match import Match
from app.models.match_view import MatchView


def serialize_match(match: Match, current_user_id: int) -> dict:
    other_user_id = (
        match.user_b_id if match.user_a_id == current_user_id else match.user_a_id
    )
    return {
        "id": match.id,
        "user_id": other_user_id,
        "status": match.status,
        "created_at": match.created_at.isoformat() if match.created_at else None,
        "closed_at": match.closed_at.isoformat() if match.closed_at else None,
    }


async def list_matches(session: AsyncSession, user_id: int, unseen: bool) -> dict:
    base_predicate = or_(Match.user_a_id == user_id, Match.user_b_id == user_id)
    stmt = select(Match).where(Match.status == "active", base_predicate)
    if unseen:
        stmt = stmt.outerjoin(
            MatchView,
            and_(MatchView.match_id == Match.id, MatchView.user_id == user_id),
        ).where(MatchView.id.is_(None))
        stmt = stmt.order_by(Match.created_at.asc())
    else:
        stmt = stmt.order_by(Match.created_at.desc())
    result = await session.execute(stmt)
    matches = list(result.scalars().all())
    payload = [serialize_match(match, user_id) for match in matches]
    return {"data": payload, "error": None}


async def close_match(session: AsyncSession, match_id: int, user_id: int) -> dict:
    async with session.begin():
        result = await session.execute(
            select(Match).where(Match.id == match_id).with_for_update()
        )
        match = result.scalar_one_or_none()
        if not match:
            return {
                "data": None,
                "error": {"code": "MATCH_NOT_FOUND", "message": "Match not found"},
            }
        if user_id not in (match.user_a_id, match.user_b_id):
            return {
                "data": None,
                "error": {
                    "code": "MATCH_FORBIDDEN",
                    "message": "Match does not belong to user",
                },
            }
        if match.status == "closed":
            return {"data": serialize_match(match, user_id), "error": None}

        match.status = "closed"
        match.closed_at = datetime.utcnow()
        other_user_id = (
            match.user_b_id if match.user_a_id == user_id else match.user_a_id
        )
        await add_audit_log(
            session,
            event_type="match_close",
            actor_id=user_id,
            target_id=other_user_id,
            metadata={"match_id": match.id},
        )

    return {"data": serialize_match(match, user_id), "error": None}


async def mark_match_seen(session: AsyncSession, match_id: int, user_id: int) -> dict:
    async with session.begin():
        result = await session.execute(
            select(Match).where(Match.id == match_id).with_for_update()
        )
        match = result.scalar_one_or_none()
        if not match:
            return {
                "data": None,
                "error": {"code": "MATCH_NOT_FOUND", "message": "Match not found"},
            }
        if user_id not in (match.user_a_id, match.user_b_id):
            return {
                "data": None,
                "error": {
                    "code": "MATCH_FORBIDDEN",
                    "message": "Match does not belong to user",
                },
            }

        insert_stmt = (
            insert(MatchView)
            .values(match_id=match.id, user_id=user_id)
            .on_conflict_do_nothing(index_elements=["match_id", "user_id"])
        )
        await session.execute(insert_stmt)

    return {"data": serialize_match(match, user_id), "error": None}
