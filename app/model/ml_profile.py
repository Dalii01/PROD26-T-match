from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UserMLProfile(Base):

    __tablename__ = "user_ml_profiles"

    # ID пользователя из сырых транзакционных данных
    party_rk: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)

    # МЕТА-ФИЧА: Общее количество транзакций
    total_transactions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ВЕКТОР ФИНАНСОВЫХ ИНТЕРЕСОВ (Нормализованные доли трат по категориям от 0 до 1)
    category_shares: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Когда последний раз обновляли
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
