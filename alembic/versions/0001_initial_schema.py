from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("external_party_rk", sa.BigInteger, unique=True, nullable=True),
        sa.Column("first_name", sa.String(length=64), nullable=False),
        sa.Column("last_name", sa.String(length=64), nullable=False),
        sa.Column("nickname", sa.String(length=64), nullable=False, unique=True),
        sa.Column("bio", sa.Text, nullable=True),
        sa.Column("gender", sa.String(length=16), nullable=True),
        sa.Column("birth_date", sa.Date, nullable=True),
        sa.Column(
            "is_active", sa.Boolean, nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "user_photos",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column(
            "is_primary", sa.Boolean, nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_user_photos_user_id", "user_photos", ["user_id"])

    op.create_table(
        "user_features",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("features", postgresql.JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "interactions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "actor_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "actor_id <> target_id", name="ck_interactions_actor_not_target"
        ),
        sa.CheckConstraint(
            "action IN ('like','skip','hide')", name="ck_interactions_action"
        ),
    )
    op.create_index("ix_interactions_actor_id", "interactions", ["actor_id"])
    op.create_index("ix_interactions_target_id", "interactions", ["target_id"])

    op.create_table(
        "matches",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "user_a_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_b_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="active"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("user_a_id < user_b_id", name="ck_matches_user_order"),
        sa.CheckConstraint("status IN ('active','closed')", name="ck_matches_status"),
        sa.UniqueConstraint("user_a_id", "user_b_id", name="uq_matches_pair"),
    )
    op.create_index("ix_matches_user_a_id", "matches", ["user_a_id"])
    op.create_index("ix_matches_user_b_id", "matches", ["user_b_id"])

    op.create_table(
        "conversations",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "match_id",
            sa.BigInteger,
            sa.ForeignKey("matches.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="active"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active','closed')", name="ck_conversations_status"
        ),
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "conversation_id",
            sa.BigInteger,
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sender_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index("ix_messages_sender_id", "messages", ["sender_id"])

    op.create_table(
        "blocks",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "blocker_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "blocked_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "blocker_id <> blocked_id", name="ck_blocks_blocker_not_blocked"
        ),
        sa.UniqueConstraint("blocker_id", "blocked_id", name="uq_blocks_pair"),
    )
    op.create_index("ix_blocks_blocker_id", "blocks", ["blocker_id"])
    op.create_index("ix_blocks_blocked_id", "blocks", ["blocked_id"])

    op.create_table(
        "reports",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "reporter_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "reported_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "reporter_id <> reported_id", name="ck_reports_reporter_not_reported"
        ),
    )
    op.create_index("ix_reports_reporter_id", "reports", ["reporter_id"])
    op.create_index("ix_reports_reported_id", "reports", ["reported_id"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column(
            "actor_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "target_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_audit_log_actor_id", "audit_log", ["actor_id"])
    op.create_index("ix_audit_log_target_id", "audit_log", ["target_id"])
    op.create_index("ix_audit_log_event_type", "audit_log", ["event_type"])

    op.create_table(
        "transactions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("real_transaction_dttm", sa.DateTime(timezone=True), nullable=False),
        sa.Column("party_rk", sa.BigInteger, nullable=False),
        sa.Column("transaction_rk", sa.BigInteger, nullable=False),
        sa.Column("merchant_type_code", sa.String(length=32), nullable=True),
        sa.Column("merchant_nm", sa.Text, nullable=True),
        sa.Column("category_nm", sa.Text, nullable=True),
    )
    op.create_index("ix_transactions_party_rk", "transactions", ["party_rk"])
    op.create_index(
        "ix_transactions_transaction_rk", "transactions", ["transaction_rk"]
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_transaction_rk", table_name="transactions")
    op.drop_index("ix_transactions_party_rk", table_name="transactions")
    op.drop_table("transactions")

    op.drop_index("ix_audit_log_event_type", table_name="audit_log")
    op.drop_index("ix_audit_log_target_id", table_name="audit_log")
    op.drop_index("ix_audit_log_actor_id", table_name="audit_log")
    op.drop_table("audit_log")

    op.drop_index("ix_reports_reported_id", table_name="reports")
    op.drop_index("ix_reports_reporter_id", table_name="reports")
    op.drop_table("reports")

    op.drop_index("ix_blocks_blocked_id", table_name="blocks")
    op.drop_index("ix_blocks_blocker_id", table_name="blocks")
    op.drop_table("blocks")

    op.drop_index("ix_messages_sender_id", table_name="messages")
    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.drop_table("messages")

    op.drop_table("conversations")

    op.drop_index("ix_matches_user_b_id", table_name="matches")
    op.drop_index("ix_matches_user_a_id", table_name="matches")
    op.drop_table("matches")

    op.drop_index("ix_interactions_target_id", table_name="interactions")
    op.drop_index("ix_interactions_actor_id", table_name="interactions")
    op.drop_table("interactions")

    op.drop_table("user_features")

    op.drop_index("ix_user_photos_user_id", table_name="user_photos")
    op.drop_table("user_photos")

    op.drop_table("users")
