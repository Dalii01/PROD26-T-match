from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    real_transaction_dttm: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    party_rk: Mapped[int] = mapped_column(BigInteger, nullable=False)
    transaction_rk: Mapped[int] = mapped_column(BigInteger, nullable=False)
    merchant_type_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    merchant_nm: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_nm: Mapped[str | None] = mapped_column(Text, nullable=True)
