"""add user_ml_profiles table

Revision ID: 0002_ml_profiles
Revises: 0001_initial_schema
Create Date: 2026-03-14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_ml_profiles"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_ml_profiles",
        # party_rk из сырых транзакций T-Bank (хеш ID пользователя).
        # Нет Foreign Key на таблицу users намеренно:
        # данные заливаются оффлайн независимо от бэкенда.
        sa.Column("party_rk", sa.String(64), primary_key=True),
        # Мета-фича: общее кол-во транзакций.
        # Используется как триггер холодного старта (< 5 → полагаемся на анкету).
        sa.Column("total_transactions", sa.Integer, nullable=False, server_default="0"),
        # Вектор финансовых интересов (нормализованные доли трат по категориям 0.0—1.0).
        # Пример: {"Рестораны": 0.45, "Транспорт": 0.30, "Другое": 0.25}
        sa.Column(
            "category_shares", postgresql.JSONB, nullable=False, server_default="{}"
        ),
        # Время последнего обновления вектора (для мониторинга свежести данных).
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # Индекс для быстрого поиска пользователя по party_rk при инференсе.
    op.create_index("ix_user_ml_profiles_party_rk", "user_ml_profiles", ["party_rk"])


def downgrade() -> None:
    op.drop_index("ix_user_ml_profiles_party_rk", table_name="user_ml_profiles")
    op.drop_table("user_ml_profiles")
