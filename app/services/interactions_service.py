from __future__ import annotations

from datetime import date
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.anti_abuse import check_and_incr_daily
from app.audit import add_audit_log
from app.models.interaction import Interaction
from app.models.match import Match
from app.models.user import User

DAILY_INTERACTIONS_LIMIT = 20


def _normalize_match_pair(actor_id: int, target_id: int) -> tuple[int, int]:
    return (actor_id, target_id) if actor_id < target_id else (target_id, actor_id)


def _calc_age(birth_date: date | None) -> int | None:
    if not birth_date:
        return None
    today = date.today()
    years = today.year - birth_date.year
    if (today.month, today.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years


def serialize_user_card(user: User) -> dict:
    primary_photo = None
    if user.photos:
        primary_photo = next((p for p in user.photos if p.is_primary), user.photos[0])
    return {
        "id": user.id,
        "name": f"{user.first_name} {user.last_name}",
        "age": _calc_age(user.birth_date),
        "photo_url": primary_photo.url if primary_photo else None,
    }


async def ensure_active_user(session: AsyncSession, user_id: int) -> bool:
    result = await session.execute(
        select(User.id).where(User.id == user_id, User.is_active.is_(True))
    )
    return result.scalar_one_or_none() is not None


async def create_interaction(
    session: AsyncSession,
    actor_id: int,
    target_id: int,
    action: Literal["like", "skip", "hide"],
    *,
    check_and_incr_daily_fn=check_and_incr_daily,
) -> dict:
    if actor_id == target_id:
        return {
            "data": None,
            "error": {
                "code": "INVALID_TARGET",
                "message": "target_id must differ from actor_id",
            },
        }

    match_created = False
    match_id: int | None = None
    is_match = False

    async with session.begin():
        lock_stmt = (
            select(User.id)
            .where(User.id.in_([actor_id, target_id]))
            .order_by(User.id)
            .with_for_update()
        )
        locked = list((await session.execute(lock_stmt)).scalars().all())
        if len(locked) != 2:
            return {
                "data": None,
                "error": {"code": "USER_NOT_FOUND", "message": "User not found"},
            }

        allowed, _ = await check_and_incr_daily_fn(
            actor_id, "interactions", 1, DAILY_INTERACTIONS_LIMIT
        )
        if not allowed:
            return {
                "data": None,
                "error": {
                    "code": "DAILY_LIMIT_REACHED",
                    "message": "Daily interactions limit reached",
                },
            }

        session.add(Interaction(actor_id=actor_id, target_id=target_id, action=action))
        await add_audit_log(
            session,
            event_type=action,
            actor_id=actor_id,
            target_id=target_id,
        )

        if action == "like":
            reciprocal_stmt = (
                select(Interaction.id)
                .where(
                    Interaction.actor_id == target_id,
                    Interaction.target_id == actor_id,
                    Interaction.action == "like",
                )
                .limit(1)
            )
            reciprocal_like = (
                await session.execute(reciprocal_stmt)
            ).scalar_one_or_none()
            if reciprocal_like is not None:
                user_a_id, user_b_id = _normalize_match_pair(actor_id, target_id)
                insert_stmt = (
                    insert(Match)
                    .values(user_a_id=user_a_id, user_b_id=user_b_id, status="active")
                    .on_conflict_do_nothing(index_elements=["user_a_id", "user_b_id"])
                    .returning(Match.id)
                )
                result = await session.execute(insert_stmt)
                match_id = result.scalar_one_or_none()
                if match_id is not None:
                    match_created = True
                    is_match = True
                    await add_audit_log(
                        session,
                        event_type="match",
                        actor_id=actor_id,
                        target_id=target_id,
                        metadata={"match_id": match_id},
                    )
                else:
                    existing_stmt = select(Match).where(
                        Match.user_a_id == user_a_id,
                        Match.user_b_id == user_b_id,
                    )
                    existing_match = (
                        await session.execute(existing_stmt)
                    ).scalar_one_or_none()
                    if existing_match and existing_match.status == "active":
                        is_match = True
                        match_id = existing_match.id

    return {
        "data": {
            "action": action,
            "is_match": is_match,
            "match_created": match_created,
            "match_id": match_id,
        },
        "error": None,
    }


async def list_liked_by(session: AsyncSession, user_id: int) -> dict:
    if not await ensure_active_user(session, user_id):
        return {
            "data": None,
            "error": {"code": "USER_NOT_FOUND", "message": "User not found"},
        }

    stmt = (
        select(User)
        .join(Interaction, Interaction.actor_id == User.id)
        .options(selectinload(User.photos))
        .where(
            Interaction.target_id == user_id,
            Interaction.action == "like",
            User.is_active.is_(True),
        )
        .distinct(User.id)
        .order_by(User.id)
    )
    result = await session.execute(stmt)
    users = list(result.scalars().all())
    return {"data": [serialize_user_card(user) for user in users], "error": None}


async def list_liked(session: AsyncSession, user_id: int) -> dict:
    if not await ensure_active_user(session, user_id):
        return {
            "data": None,
            "error": {"code": "USER_NOT_FOUND", "message": "User not found"},
        }

    stmt = (
        select(User)
        .join(Interaction, Interaction.target_id == User.id)
        .options(selectinload(User.photos))
        .where(
            Interaction.actor_id == user_id,
            Interaction.action == "like",
            User.is_active.is_(True),
        )
        .distinct(User.id)
        .order_by(User.id)
    )
    result = await session.execute(stmt)
    users = list(result.scalars().all())
    return {"data": [serialize_user_card(user) for user in users], "error": None}


async def remove_like(session: AsyncSession, actor_id: int, target_id: int) -> dict:
    if actor_id == target_id:
        return {
            "data": None,
            "error": {
                "code": "INVALID_TARGET",
                "message": "target_id must differ from actor_id",
            },
        }

    async with session.begin():
        lock_stmt = (
            select(User.id)
            .where(User.id.in_([actor_id, target_id]))
            .order_by(User.id)
            .with_for_update()
        )
        locked = list((await session.execute(lock_stmt)).scalars().all())
        if len(locked) != 2:
            return {
                "data": None,
                "error": {"code": "USER_NOT_FOUND", "message": "User not found"},
            }

        user_a_id, user_b_id = _normalize_match_pair(actor_id, target_id)
        match_stmt = (
            select(Match.id)
            .where(
                Match.user_a_id == user_a_id,
                Match.user_b_id == user_b_id,
                Match.status == "active",
            )
            .limit(1)
        )
        match_id = (await session.execute(match_stmt)).scalar_one_or_none()
        if match_id is not None:
            return {
                "data": None,
                "error": {"code": "MATCH_EXISTS", "message": "Match already exists"},
            }

        delete_stmt = (
            delete(Interaction)
            .where(
                Interaction.actor_id == actor_id,
                Interaction.target_id == target_id,
                Interaction.action == "like",
            )
            .returning(Interaction.id)
        )
        result = await session.execute(delete_stmt)
        removed_count = len(result.scalars().all())
        if removed_count == 0:
            return {
                "data": None,
                "error": {"code": "LIKE_NOT_FOUND", "message": "Like not found"},
            }

        await add_audit_log(
            session,
            event_type="unlike",
            actor_id=actor_id,
            target_id=target_id,
        )

    return {"data": {"removed": True, "removed_count": removed_count}, "error": None}
