from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.anti_abuse import check_and_incr_daily
from app.services import interactions_service

router = APIRouter()


class InteractionRequest(BaseModel):
    target_id: int = Field(..., ge=1)
    action: Literal["like", "skip", "hide"]


@router.post("")
async def create_interaction(
    payload: InteractionRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    actor_id = getattr(request.state, "user_id", None)
    if not actor_id:
        return {
            "data": None,
            "error": {
                "code": "USER_ID_REQUIRED",
                "message": "X-User-ID header required",
            },
        }

    return await interactions_service.create_interaction(
        session=session,
        actor_id=actor_id,
        target_id=payload.target_id,
        action=payload.action,
        check_and_incr_daily_fn=check_and_incr_daily,
    )


@router.get("/liked-by/{user_id}")
async def list_liked_by(
    user_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    return await interactions_service.list_liked_by(session, user_id)


@router.get("/liked/{user_id}")
async def list_liked(
    user_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    return await interactions_service.list_liked(session, user_id)


@router.delete("/like/{target_id}")
async def remove_like(
    target_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    actor_id = getattr(request.state, "user_id", None)
    if not actor_id:
        return {
            "data": None,
            "error": {
                "code": "USER_ID_REQUIRED",
                "message": "X-User-ID header required",
            },
        }

    return await interactions_service.remove_like(session, actor_id, target_id)
