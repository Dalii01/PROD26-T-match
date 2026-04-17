from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.models.audit_log import AuditLog
from app.security import require_admin

router = APIRouter()


def _make_error(code: str, message: str) -> dict:
    return {"data": None, "error": {"code": code, "message": message}}


def _serialize_audit_log(item: AuditLog) -> dict:
    return {
        "id": item.id,
        "event_type": item.event_type,
        "actor_id": item.actor_id,
        "target_id": item.target_id,
        "metadata": item.metadata_ or {},
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


@router.get("")
async def list_audit_log(
    request: Request,
    actor_id: int | None = Query(default=None, ge=1),
    target_id: int | None = Query(default=None, ge=1),
    event_type: str | None = Query(default=None, min_length=1, max_length=32),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return _make_error("USER_ID_REQUIRED", "X-User-ID header required")

    _, error = await require_admin(session, user_id)
    if error:
        return _make_error(*error)

    stmt = select(AuditLog)
    if actor_id is not None:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if target_id is not None:
        stmt = stmt.where(AuditLog.target_id == target_id)
    if event_type is not None:
        stmt = stmt.where(AuditLog.event_type == event_type)

    stmt = (
        stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return {"data": [_serialize_audit_log(item) for item in rows], "error": None}
