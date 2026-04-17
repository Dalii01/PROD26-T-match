from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Interaction(Base):
    __tablename__ = "interactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    target_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    actor = relationship("User", foreign_keys=[actor_id])
    target = relationship("User", foreign_keys=[target_id])

    __table_args__ = (
        CheckConstraint(
            "actor_id <> target_id", name="ck_interactions_actor_not_target"
        ),
        CheckConstraint(
            "action IN ('like','skip','hide')", name="ck_interactions_action"
        ),
    )
