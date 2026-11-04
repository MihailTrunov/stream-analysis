"""Retain shared history range requests independently of active imports."""

import sqlalchemy as sa

from alembic import op

revision = "20261104_13"
down_revision = "20261104_12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "history_range_requests",
        sa.Column("request_id", sa.String(36), primary_key=True),
        sa.Column("request_key", sa.String(64), nullable=False, unique=True),
        sa.Column("feed_key", sa.String(64), nullable=False),
        sa.Column("feed_json", sa.Text(), nullable=False),
        sa.Column("range_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("range_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled", sa.Boolean(), nullable=False),
        sa.Column("failure_reason", sa.Text()),
        sa.CheckConstraint("range_end > range_start", name="ck_history_range_bounds"),
    )
    op.create_index("ix_history_range_pending", "history_range_requests", ["settled", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_history_range_pending", table_name="history_range_requests")
    op.drop_table("history_range_requests")
