from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import add_audit_log
from app.models.block import Block
from app.models.conversation import Conversation
from app.models.match import Match
from app.models.user import User
from app.security import require_admin


async def block_user(session: AsyncSession, actor_id: int, target_id: int) -> dict:
    _, error = await require_admin(session, actor_id)
    if error:
        return {"data": None, "error": {"code": error[0], "message": error[1]}}

    if target_id == actor_id:
        return {
            "data": None,
            "error": {
                "code": "INVALID_TARGET",
                "message": "target_id must differ from actor_id",
            },
        }

    async with session.begin():
        target = await session.get(User, target_id, with_for_update=True)
        if not target:
            return {
                "data": None,
                "error": {"code": "USER_NOT_FOUND", "message": "User not found"},
            }

        insert_stmt = (
            insert(Block)
            .values(blocker_id=actor_id, blocked_id=target_id)
            .on_conflict_do_nothing(index_elements=["blocker_id", "blocked_id"])
        )
        await session.execute(insert_stmt)

        target.is_active = False

        match_stmt = (
            select(Match)
            .where(
                Match.status == "active",
                or_(
                    Match.user_a_id == target_id,
                    Match.user_b_id == target_id,
                ),
            )
            .with_for_update()
        )
        matches = list((await session.execute(match_stmt)).scalars().all())
        now = datetime.utcnow()
        for match in matches:
            match.status = "closed"
            match.closed_at = now

        if matches:
            match_ids = [match.id for match in matches]
            await session.execute(
                update(Conversation)
                .where(
                    Conversation.match_id.in_(match_ids),
                    Conversation.status == "active",
                )
                .values(status="closed", closed_at=now)
            )

        await add_audit_log(
            session,
            event_type="block",
            actor_id=actor_id,
            target_id=target_id,
            metadata=None,
        )

    return {"data": {"blocked_id": target_id}, "error": None}
