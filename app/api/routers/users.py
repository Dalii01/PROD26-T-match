from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.anti_abuse import check_and_incr_daily
from app.services import users_service

_get_user_by_id = users_service._get_user_by_id

router = APIRouter()


class PhotoCreateRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048)
    is_primary: bool | None = None


@router.get("")
async def list_users(
    limit: int = Query(default=10, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await users_service.list_users(session, limit)


@router.post("/me/photos")
async def add_my_photo(
    payload: PhotoCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    raw_user_id = request.headers.get("X-User-ID")
    if not raw_user_id:
        return {
            "data": None,
            "error": {
                "code": "USER_ID_REQUIRED",
                "message": "X-User-ID header required",
            },
        }
    try:
        actor_id = int(raw_user_id)
    except ValueError:
        return {
            "data": None,
            "error": {
                "code": "USER_ID_INVALID",
                "message": "X-User-ID must be integer",
            },
        }

    return await users_service.add_my_photo(
        session=session,
        actor_id=actor_id,
        url=payload.url,
        is_primary=payload.is_primary,
    )


@router.delete("/me/photos")
async def delete_my_photo(
    request: Request,
    url: str = Query(..., min_length=1, max_length=2048),
    session: AsyncSession = Depends(get_session),
) -> dict:
    raw_user_id = request.headers.get("X-User-ID")
    if not raw_user_id:
        return {
            "data": None,
            "error": {
                "code": "USER_ID_REQUIRED",
                "message": "X-User-ID header required",
            },
        }
    try:
        actor_id = int(raw_user_id)
    except ValueError:
        return {
            "data": None,
            "error": {
                "code": "USER_ID_INVALID",
                "message": "X-User-ID must be integer",
            },
        }

    return await users_service.delete_my_photo(session, actor_id, url)


@router.get("/me")
async def get_my_profile(
    request: Request, session: AsyncSession = Depends(get_session)
) -> dict:
    raw_user_id = request.headers.get("X-User-ID")
    if not raw_user_id:
        return {
            "data": None,
            "error": {
                "code": "USER_ID_REQUIRED",
                "message": "X-User-ID header required",
            },
        }
    try:
        user_id = int(raw_user_id)
    except ValueError:
        return {
            "data": None,
            "error": {
                "code": "USER_ID_INVALID",
                "message": "X-User-ID must be integer",
            },
        }
    return await users_service._get_my_profile(
        session=session,
        user_id=user_id,
        get_user_by_id_fn=_get_user_by_id,
    )


@router.put("/admin/{user_id}")
async def grant_admin(
    user_id: int, request: Request, session: AsyncSession = Depends(get_session)
) -> dict:
    raw_user_id = request.headers.get("X-User-ID")
    if not raw_user_id:
        return {
            "data": None,
            "error": {
                "code": "USER_ID_REQUIRED",
                "message": "X-User-ID header required",
            },
        }
    try:
        actor_id = int(raw_user_id)
    except ValueError:
        return {
            "data": None,
            "error": {
                "code": "USER_ID_INVALID",
                "message": "X-User-ID must be integer",
            },
        }

    return await users_service.grant_admin(session, actor_id, user_id)


@router.get("/{user_id}")
async def get_user_by_id(
    user_id: int, request: Request, session: AsyncSession = Depends(get_session)
) -> dict:
    raw_user_id = request.headers.get("X-User-ID")
    actor_id: int | None = None
    if raw_user_id:
        try:
            actor_id = int(raw_user_id)
        except ValueError:
            return {
                "data": None,
                "error": {
                    "code": "USER_ID_INVALID",
                    "message": "X-User-ID must be integer",
                },
            }

    return await users_service._get_user_by_id_api(
        session=session,
        actor_id=actor_id,
        user_id=user_id,
        get_user_by_id_fn=_get_user_by_id,
        check_and_incr_daily_fn=check_and_incr_daily,
    )
