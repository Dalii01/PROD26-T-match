from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.services import blocks_service

router = APIRouter()


class BlockRequest(BaseModel):
    target_id: int = Field(..., ge=1)


@router.post("")
async def block_user(
    payload: BlockRequest,
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

    return await blocks_service.block_user(session, actor_id, payload.target_id)
