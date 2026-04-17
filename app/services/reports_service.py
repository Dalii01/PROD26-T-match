from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import add_audit_log
from app.models.report import Report
from app.models.user import User
from app.security import require_active_user, require_admin

ALLOWED_REASONS = {
    "spam",
    "abuse",
    "fraud",
    "harassment",
    "other",
    "спам",
    "оскорбление",
    "мошенничество",
    "домогательство",
    "другое",
}


def _normalize_reason(reason: str) -> str:
    return reason.strip().lower()


def serialize_report(report: Report) -> dict:
    return {
        "id": report.id,
        "reported_id": report.reported_id,
        "reporter_id": report.reporter_id,
        "reason": report.reason,
        "comment": report.comment,
        "created_at": report.created_at.isoformat() if report.created_at else None,
    }


async def create_report(
    session: AsyncSession,
    reporter_id: int,
    reported_id: int,
    reason: str,
    comment: str | None,
) -> dict:
    _, error = await require_active_user(session, reporter_id)
    if error:
        return {"data": None, "error": {"code": error[0], "message": error[1]}}

    if reporter_id == reported_id:
        return {
            "data": None,
            "error": {
                "code": "INVALID_TARGET",
                "message": "reported_id must differ from reporter_id",
            },
        }

    normalized = _normalize_reason(reason)
    if normalized not in ALLOWED_REASONS:
        return {
            "data": None,
            "error": {"code": "INVALID_REASON", "message": "Unsupported report reason"},
        }

    async with session.begin():
        reported = await session.get(User, reported_id)
        if not reported:
            return {
                "data": None,
                "error": {"code": "USER_NOT_FOUND", "message": "User not found"},
            }

        report = Report(
            reporter_id=reporter_id,
            reported_id=reported_id,
            reason=normalized,
            comment=comment,
        )
        session.add(report)
        await session.flush()

        await add_audit_log(
            session,
            event_type="report",
            actor_id=reporter_id,
            target_id=reported_id,
            metadata={"reason": normalized, "comment": comment},
        )

    return {"data": serialize_report(report), "error": None}


async def list_reports(session: AsyncSession, actor_id: int) -> dict:
    _, error = await require_admin(session, actor_id)
    if error:
        return {"data": None, "error": {"code": error[0], "message": error[1]}}

    stmt = select(Report).order_by(Report.created_at.desc(), Report.id.desc())
    reports = list((await session.execute(stmt)).scalars().all())
    return {"data": [serialize_report(report) for report in reports], "error": None}


async def reject_report(session: AsyncSession, actor_id: int, report_id: int) -> dict:
    _, error = await require_admin(session, actor_id)
    if error:
        return {"data": None, "error": {"code": error[0], "message": error[1]}}

    async with session.begin():
        result = await session.execute(
            delete(Report).where(Report.id == report_id).returning(Report.id)
        )
        removed_id = result.scalar_one_or_none()
        if removed_id is None:
            return {
                "data": None,
                "error": {"code": "REPORT_NOT_FOUND", "message": "Report not found"},
            }

    return {"data": {"report_id": report_id}, "error": None}
