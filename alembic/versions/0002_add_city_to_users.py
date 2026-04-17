"""add city to users

Revision ID: 0002_add_city_to_users
Revises: 0001_initial_schema
Create Date: 2026-03-14
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_add_city_to_users"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("city", sa.String(length=128), nullable=True))
    op.create_index("ix_users_city", "users", ["city"])


def downgrade() -> None:
    op.drop_index("ix_users_city", table_name="users")
    op.drop_column("users", "city")
