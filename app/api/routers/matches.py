from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.services import matches_service

router = APIRouter()


@router.get("")
async def list_matches(
    request: Request,
    unseen: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return {
            "data": None,
            "error": {
                "code": "USER_ID_REQUIRED",
                "message": "X-User-ID header required",
            },
        }

    return await matches_service.list_matches(session, user_id, unseen)


@router.patch("/{match_id}/close")
async def close_match(
    match_id: int, request: Request, session: AsyncSession = Depends(get_session)
) -> dict:
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return {
            "data": None,
            "error": {
                "code": "USER_ID_REQUIRED",
                "message": "X-User-ID header required",
            },
        }

    return await matches_service.close_match(session, match_id, user_id)


@router.patch("/{match_id}/seen")
async def mark_match_seen(
    match_id: int, request: Request, session: AsyncSession = Depends(get_session)
) -> dict:
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return {
            "data": None,
            "error": {
                "code": "USER_ID_REQUIRED",
                "message": "X-User-ID header required",
            },
        }

    return await matches_service.mark_match_seen(session, match_id, user_id)
