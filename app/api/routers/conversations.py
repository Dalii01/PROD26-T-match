from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.services import conversations_service

router = APIRouter()


class ConversationCreateRequest(BaseModel):
    match_id: int = Field(..., ge=1)


class MessageCreateRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=2000)


@router.get("")
async def list_conversations(
    request: Request, session: AsyncSession = Depends(get_session)
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

    return await conversations_service.list_conversations(session, user_id)


@router.post("", response_model=None)
async def create_conversation(
    payload: ConversationCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict | JSONResponse:
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return {
            "data": None,
            "error": {
                "code": "USER_ID_REQUIRED",
                "message": "X-User-ID header required",
            },
        }

    return await conversations_service.create_conversation(
        session=session, user_id=user_id, match_id=payload.match_id
    )


@router.get("/{conversation_id}/messages", response_model=None)
async def list_messages(
    conversation_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict | JSONResponse:
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return {
            "data": None,
            "error": {
                "code": "USER_ID_REQUIRED",
                "message": "X-User-ID header required",
            },
        }

    return await conversations_service.list_messages(
        session=session, user_id=user_id, conversation_id=conversation_id
    )


@router.post("/{conversation_id}/messages", response_model=None)
async def create_message(
    conversation_id: int,
    payload: MessageCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict | JSONResponse:
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return {
            "data": None,
            "error": {
                "code": "USER_ID_REQUIRED",
                "message": "X-User-ID header required",
            },
        }

    return await conversations_service.create_message(
        session=session,
        user_id=user_id,
        conversation_id=conversation_id,
        body=payload.body,
    )
