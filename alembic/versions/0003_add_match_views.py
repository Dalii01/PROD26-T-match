from alembic import op
import sqlalchemy as sa

revision = "0003_add_match_views"
down_revision = "0002_add_city_to_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "match_views",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "match_id",
            sa.BigInteger,
            sa.ForeignKey("matches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "shown_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("match_id", "user_id", name="uq_match_views_pair"),
    )
    op.create_index("ix_match_views_match_id", "match_views", ["match_id"])
    op.create_index("ix_match_views_user_id", "match_views", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_match_views_user_id", table_name="match_views")
    op.drop_index("ix_match_views_match_id", table_name="match_views")
    op.drop_table("match_views")
