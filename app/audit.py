from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


async def add_audit_log(
    session: AsyncSession,
    event_type: str,
    actor_id: int | None,
    target_id: int | None,
    metadata: dict | None = None,
) -> None:
    session.add(
        AuditLog(
            event_type=event_type,
            actor_id=actor_id,
            target_id=target_id,
            metadata_=metadata,
        )
    )
