from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.audit import add_audit_log
from app.anti_abuse import check_and_incr_daily
from app.models.user import User, UserFeatures, UserPhoto
from app.security import admin_exists, require_active_user, require_admin

DAILY_PROFILE_VIEWS_LIMIT = 20
MAX_PROFILE_PHOTOS = 5


def _calc_age(birth_date: date | None) -> int | None:
    if not birth_date:
        return None
    today = date.today()
    years = today.year - birth_date.year
    if (today.month, today.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years


def _serialize_user_card(user: User) -> dict:
    primary_photo = None
    if user.photos:
        primary_photo = next((p for p in user.photos if p.is_primary), user.photos[0])
    return {
        "id": user.id,
        "name": f"{user.first_name} {user.last_name}",
        "age": _calc_age(user.birth_date),
        "photo_url": primary_photo.url if primary_photo else None,
    }


def _serialize_profile(user: User) -> dict:
    primary_photo = None
    if user.photos:
        primary_photo = next((p for p in user.photos if p.is_primary), user.photos[0])

    tags: list[str] = []
    if isinstance(user.features, UserFeatures) and isinstance(
        user.features.features, dict
    ):
        raw_tags = user.features.features.get("tags")
        if isinstance(raw_tags, list):
            tags = [str(tag) for tag in raw_tags]

    return {
        "id": user.id,
        "name": f"{user.first_name} {user.last_name}",
        "nickname": user.nickname,
        "bio": user.bio,
        "gender": user.gender,
        "city": user.city,
        "age": _calc_age(user.birth_date),
        "photos": [
            {"url": photo.url, "is_primary": photo.is_primary}
            for photo in (user.photos or [])
        ],
        "primary_photo_url": primary_photo.url if primary_photo else None,
        "tags": tags,
    }


async def _fetch_users(session: AsyncSession, limit: int | None) -> list[User]:
    stmt = (
        select(User)
        .options(selectinload(User.photos))
        .where(User.is_active.is_(True))
        .order_by(User.id)
    )
    if limit:
        stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _get_user_by_id(session: AsyncSession, user_id: int) -> User | None:
    result = await session.execute(
        select(User)
        .options(selectinload(User.photos), selectinload(User.features))
        .where(User.id == user_id, User.is_active.is_(True))
    )
    return result.scalars().first()


async def list_users(session: AsyncSession, limit: int) -> dict:
    users = await _fetch_users(session, limit)
    payload = [_serialize_user_card(user) for user in users]
    return {"data": payload, "error": None}


async def add_my_photo(
    session: AsyncSession, actor_id: int, url: str, is_primary: bool | None
) -> dict:
    _, error = await require_active_user(session, actor_id)
    if error:
        return {"data": None, "error": {"code": error[0], "message": error[1]}}

    async with session.begin():
        user = await session.get(User, actor_id, with_for_update=True)
        if not user:
            return {
                "data": None,
                "error": {"code": "USER_NOT_FOUND", "message": "User not found"},
            }

        photos = list(
            (
                await session.execute(
                    select(UserPhoto).where(UserPhoto.user_id == actor_id)
                )
            )
            .scalars()
            .all()
        )

        if any(photo.url == url for photo in photos):
            return {
                "data": None,
                "error": {"code": "DUPLICATE_PHOTO", "message": "Photo already exists"},
            }

        if len(photos) >= MAX_PROFILE_PHOTOS:
            return {
                "data": None,
                "error": {
                    "code": "PHOTO_LIMIT_REACHED",
                    "message": f"Maximum {MAX_PROFILE_PHOTOS} photos allowed",
                },
            }

        make_primary = is_primary is True
        if not make_primary:
            make_primary = not any(photo.is_primary for photo in photos)

        if make_primary:
            for photo in photos:
                photo.is_primary = False

        new_photo = UserPhoto(user_id=actor_id, url=url, is_primary=make_primary)
        session.add(new_photo)
        await session.flush()

        await add_audit_log(
            session,
            event_type="photo_add",
            actor_id=actor_id,
            target_id=actor_id,
            metadata={"url": url, "is_primary": make_primary},
        )

    return {
        "data": {"url": new_photo.url, "is_primary": new_photo.is_primary},
        "error": None,
    }


async def delete_my_photo(session: AsyncSession, actor_id: int, url: str) -> dict:
    _, error = await require_active_user(session, actor_id)
    if error:
        return {"data": None, "error": {"code": error[0], "message": error[1]}}

    async with session.begin():
        user = await session.get(User, actor_id, with_for_update=True)
        if not user:
            return {
                "data": None,
                "error": {"code": "USER_NOT_FOUND", "message": "User not found"},
            }

        result = await session.execute(
            select(UserPhoto)
            .where(UserPhoto.user_id == actor_id, UserPhoto.url == url)
            .limit(1)
        )
        photo = result.scalar_one_or_none()
        if not photo:
            return {
                "data": None,
                "error": {"code": "PHOTO_NOT_FOUND", "message": "Photo not found"},
            }

        was_primary = photo.is_primary
        await session.delete(photo)
        await session.flush()

        if was_primary:
            next_photo = (
                await session.execute(
                    select(UserPhoto)
                    .where(UserPhoto.user_id == actor_id)
                    .order_by(UserPhoto.id)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if next_photo:
                next_photo.is_primary = True

        await add_audit_log(
            session,
            event_type="photo_delete",
            actor_id=actor_id,
            target_id=actor_id,
            metadata={"url": url, "was_primary": was_primary},
        )

    return {"data": {"url": url}, "error": None}


async def get_my_profile(session: AsyncSession, user_id: int) -> dict:
    return await _get_my_profile(
        session=session,
        user_id=user_id,
        get_user_by_id_fn=_get_user_by_id,
    )


async def _get_my_profile(
    session: AsyncSession,
    user_id: int,
    *,
    get_user_by_id_fn,
) -> dict:
    user = await get_user_by_id_fn(session, user_id)
    if not user:
        return {
            "data": None,
            "error": {"code": "USER_NOT_FOUND", "message": "User not found"},
        }
    return {"data": _serialize_profile(user), "error": None}


async def grant_admin(session: AsyncSession, actor_id: int, user_id: int) -> dict:
    if user_id <= 0:
        return {
            "data": None,
            "error": {"code": "INVALID_TARGET", "message": "Invalid user id"},
        }

    _, error = await require_active_user(session, actor_id)
    if error:
        return {"data": None, "error": {"code": error[0], "message": error[1]}}

    if await admin_exists(session):
        _, admin_error = await require_admin(session, actor_id)
        if admin_error:
            return {
                "data": None,
                "error": {"code": admin_error[0], "message": admin_error[1]},
            }

    async with session.begin():
        target = await session.get(User, user_id, with_for_update=True)
        if not target:
            return {
                "data": None,
                "error": {"code": "USER_NOT_FOUND", "message": "User not found"},
            }
        target.is_admin = True

    return {"data": {"id": user_id, "is_admin": True}, "error": None}


async def get_user_by_id(
    session: AsyncSession, actor_id: int | None, user_id: int
) -> dict:
    return await _get_user_by_id_api(
        session=session,
        actor_id=actor_id,
        user_id=user_id,
        get_user_by_id_fn=_get_user_by_id,
        check_and_incr_daily_fn=check_and_incr_daily,
    )


async def _get_user_by_id_api(
    session: AsyncSession,
    actor_id: int | None,
    user_id: int,
    *,
    get_user_by_id_fn,
    check_and_incr_daily_fn,
) -> dict:
    if actor_id is not None:
        allowed, _ = await check_and_incr_daily_fn(
            actor_id, "profile_views", 1, DAILY_PROFILE_VIEWS_LIMIT
        )
        if not allowed:
            return {
                "data": None,
                "error": {
                    "code": "DAILY_LIMIT_REACHED",
                    "message": "Daily profile views limit reached",
                },
            }

    user = await get_user_by_id_fn(session, user_id)
    if not user:
        return {
            "data": None,
            "error": {"code": "USER_NOT_FOUND", "message": "User not found"},
        }
    return {"data": _serialize_profile(user), "error": None}
