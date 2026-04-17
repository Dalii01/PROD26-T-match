from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.services import reports_service

router = APIRouter()


class ReportCreateRequest(BaseModel):
    reported_id: int = Field(..., ge=1)
    reason: str = Field(..., min_length=1, max_length=64)
    comment: str | None = Field(default=None, max_length=1000)


class ReportRejectRequest(BaseModel):
    report_id: int = Field(..., ge=1)


@router.post("")
async def create_report(
    payload: ReportCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    reporter_id = getattr(request.state, "user_id", None)
    if not reporter_id:
        return {
            "data": None,
            "error": {
                "code": "USER_ID_REQUIRED",
                "message": "X-User-ID header required",
            },
        }

    return await reports_service.create_report(
        session=session,
        reporter_id=reporter_id,
        reported_id=payload.reported_id,
        reason=payload.reason,
        comment=payload.comment,
    )


@router.get("")
async def list_reports(
    request: Request, session: AsyncSession = Depends(get_session)
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

    return await reports_service.list_reports(session, actor_id)


@router.post("/reject")
async def reject_report(
    payload: ReportRejectRequest,
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

    return await reports_service.reject_report(session, actor_id, payload.report_id)
