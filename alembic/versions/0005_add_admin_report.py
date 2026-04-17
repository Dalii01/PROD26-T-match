from alembic import op
import sqlalchemy as sa

revision = "0005_add_admin_report"
down_revision = "0004_merge_all_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_admin", sa.Boolean, nullable=False, server_default=sa.text("false")
        ),
    )
    op.add_column("reports", sa.Column("comment", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("reports", "comment")
    op.drop_column("users", "is_admin")
