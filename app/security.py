from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


async def require_active_user(
    session: AsyncSession, user_id: int
) -> tuple[User | None, tuple[str, str] | None]:
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return None, ("USER_NOT_FOUND", "User not found")
    if not user.is_active:
        return None, ("USER_BLOCKED", "User is blocked")
    return user, None


async def require_admin(
    session: AsyncSession, user_id: int
) -> tuple[User | None, tuple[str, str] | None]:
    user, error = await require_active_user(session, user_id)
    if error:
        return None, error
    assert user is not None
    if not user.is_admin:
        return None, ("ADMIN_REQUIRED", "Admin privileges required")
    return user, None


async def admin_exists(session: AsyncSession) -> bool:
    result = await session.execute(
        select(User.id).where(User.is_admin.is_(True)).limit(1)
    )
    return result.scalar_one_or_none() is not None
