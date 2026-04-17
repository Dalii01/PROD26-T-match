from __future__ import annotations

from fastapi.responses import JSONResponse
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import add_audit_log
from app.models.block import Block
from app.models.conversation import Conversation
from app.models.match import Match
from app.models.message import Message


def _make_error(code: str, message: str) -> dict:
    return {"data": None, "error": {"code": code, "message": message}}


def _forbidden(code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=403, content=_make_error(code, message))


def serialize_conversation(
    conversation: Conversation, match: Match, user_id: int
) -> dict:
    other_user_id = match.user_b_id if match.user_a_id == user_id else match.user_a_id
    return {
        "id": conversation.id,
        "match_id": match.id,
        "user_id": other_user_id,
        "status": conversation.status,
        "created_at": (
            conversation.created_at.isoformat() if conversation.created_at else None
        ),
        "closed_at": (
            conversation.closed_at.isoformat() if conversation.closed_at else None
        ),
    }


def serialize_message(message: Message) -> dict:
    return {
        "id": message.id,
        "sender_id": message.sender_id,
        "body": message.body,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


async def _is_blocked(session: AsyncSession, user_a_id: int, user_b_id: int) -> bool:
    stmt = (
        select(Block.id)
        .where(
            or_(
                and_(
                    Block.blocker_id == user_a_id,
                    Block.blocked_id == user_b_id,
                ),
                and_(
                    Block.blocker_id == user_b_id,
                    Block.blocked_id == user_a_id,
                ),
            )
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def _load_conversation_match(
    session: AsyncSession, conversation_id: int
) -> tuple[Conversation, Match] | None:
    stmt = (
        select(Conversation, Match)
        .join(Match, Conversation.match_id == Match.id)
        .where(Conversation.id == conversation_id)
    )
    row = (await session.execute(stmt)).first()
    if not row:
        return None
    return row[0], row[1]


async def list_conversations(session: AsyncSession, user_id: int) -> dict:
    block_exists = exists().where(
        or_(
            and_(
                Block.blocker_id == Match.user_a_id,
                Block.blocked_id == Match.user_b_id,
            ),
            and_(
                Block.blocker_id == Match.user_b_id,
                Block.blocked_id == Match.user_a_id,
            ),
        )
    )

    stmt = (
        select(Conversation, Match)
        .join(Match, Conversation.match_id == Match.id)
        .where(
            Conversation.status == "active",
            Match.status == "active",
            or_(Match.user_a_id == user_id, Match.user_b_id == user_id),
            ~block_exists,
        )
        .order_by(Conversation.created_at.desc())
    )
    rows = (await session.execute(stmt)).all()
    payload = [serialize_conversation(conv, match, user_id) for conv, match in rows]
    return {"data": payload, "error": None}


async def create_conversation(
    session: AsyncSession, user_id: int, match_id: int
) -> dict | JSONResponse:
    match = (
        await session.execute(select(Match).where(Match.id == match_id))
    ).scalar_one_or_none()
    if not match:
        return _forbidden("MATCH_NOT_FOUND", "Match not found")
    if user_id not in (match.user_a_id, match.user_b_id):
        return _forbidden("MATCH_FORBIDDEN", "Match does not belong to user")
    if match.status != "active":
        return _forbidden("MATCH_INACTIVE", "Match is closed")
    if await _is_blocked(session, match.user_a_id, match.user_b_id):
        return _forbidden("MATCH_BLOCKED", "Match is blocked")

    async with session.begin():
        insert_stmt = (
            insert(Conversation)
            .values(match_id=match.id, status="active")
            .on_conflict_do_nothing(index_elements=["match_id"])
            .returning(Conversation.id)
        )
        result = await session.execute(insert_stmt)
        conversation_id = result.scalar_one_or_none()
        if conversation_id is None:
            conversation = (
                await session.execute(
                    select(Conversation).where(Conversation.match_id == match.id)
                )
            ).scalar_one_or_none()
        else:
            conversation = await session.get(Conversation, conversation_id)

        if not conversation:
            return _make_error(
                "CONVERSATION_NOT_FOUND", "Conversation could not be created"
            )
        if conversation.status != "active":
            return _forbidden("CONVERSATION_CLOSED", "Conversation is closed")

        if conversation_id is not None:
            other_user_id = (
                match.user_b_id if match.user_a_id == user_id else match.user_a_id
            )
            await add_audit_log(
                session,
                event_type="chat_open",
                actor_id=user_id,
                target_id=other_user_id,
                metadata={"match_id": match.id, "conversation_id": conversation.id},
            )

    return {
        "data": serialize_conversation(conversation, match, user_id),
        "error": None,
    }


async def list_messages(
    session: AsyncSession, user_id: int, conversation_id: int
) -> dict | JSONResponse:
    loaded = await _load_conversation_match(session, conversation_id)
    if not loaded:
        return _make_error("CONVERSATION_NOT_FOUND", "Conversation not found")
    conversation, match = loaded

    if user_id not in (match.user_a_id, match.user_b_id):
        return _forbidden(
            "CONVERSATION_FORBIDDEN", "Conversation does not belong to user"
        )
    if match.status != "active" or conversation.status != "active":
        return _forbidden("CONVERSATION_CLOSED", "Conversation is closed")
    if await _is_blocked(session, match.user_a_id, match.user_b_id):
        return _forbidden("CONVERSATION_BLOCKED", "Conversation is blocked")

    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    messages = list((await session.execute(stmt)).scalars().all())
    payload = [serialize_message(message) for message in messages]
    return {"data": payload, "error": None}


async def create_message(
    session: AsyncSession, user_id: int, conversation_id: int, body: str
) -> dict | JSONResponse:
    loaded = await _load_conversation_match(session, conversation_id)
    if not loaded:
        return _make_error("CONVERSATION_NOT_FOUND", "Conversation not found")
    conversation, match = loaded

    if user_id not in (match.user_a_id, match.user_b_id):
        return _forbidden(
            "CONVERSATION_FORBIDDEN", "Conversation does not belong to user"
        )
    if match.status != "active" or conversation.status != "active":
        return _forbidden("CONVERSATION_CLOSED", "Conversation is closed")
    if await _is_blocked(session, match.user_a_id, match.user_b_id):
        return _forbidden("CONVERSATION_BLOCKED", "Conversation is blocked")

    async with session.begin():
        message = Message(
            conversation_id=conversation.id,
            sender_id=user_id,
            body=body,
        )
        session.add(message)
        await session.flush()

    return {"data": serialize_message(message), "error": None}
