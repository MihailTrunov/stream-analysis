"""Durable checkpointed historical-import jobs.

Revision ID: 20260929_05
Revises: 20260927_04
"""

import sqlalchemy as sa

from alembic import op

revision = "20260929_05"
down_revision = "20260927_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "import_jobs",
        sa.Column("job_id", sa.String(36), primary_key=True),
        sa.Column("request_key", sa.String(64), nullable=False),
        sa.Column("dataset_id", sa.String(200), nullable=False),
        sa.Column("revision_id", sa.String(36), nullable=False, unique=True),
        sa.Column(
            "instrument_id",
            sa.String(200),
            sa.ForeignKey("instruments.instrument_id"),
            nullable=False,
        ),
        sa.Column("timeframe", sa.String(10), nullable=False),
        sa.Column("requested_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("environment", sa.String(100), nullable=False),
        sa.Column("provider_symbol", sa.String(200), nullable=False),
        sa.Column("account_fingerprint", sa.String(64)),
        sa.Column("calendar_version", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("active_slot", sa.Integer(), unique=True),
        sa.Column("source_id", sa.String(200)),
        sa.Column("next_page_token", sa.Text()),
        sa.Column("fetch_complete", sa.Boolean(), nullable=False),
        sa.Column("pages_committed", sa.Integer(), nullable=False),
        sa.Column("bar_count", sa.Integer(), nullable=False),
        sa.Column("actual_start", sa.DateTime(timezone=True)),
        sa.Column("actual_end", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("retrieved_at", sa.DateTime(timezone=True)),
        sa.Column("gap_report_json", sa.Text()),
        sa.Column("failure_reason", sa.Text()),
        sa.CheckConstraint("requested_end > requested_start", name="ck_import_requested_range"),
        sa.CheckConstraint("timeframe = '1m'", name="ck_import_mvp_timeframe"),
        sa.CheckConstraint("pages_committed >= 0 AND bar_count >= 0", name="ck_import_progress"),
        sa.CheckConstraint(
            "status IN ('queued','running','interrupted','failed','completed')",
            name="ck_import_status",
        ),
        sa.CheckConstraint(
            "(status IN ('queued','running') AND active_slot IS NOT NULL AND active_slot = 1) OR "
            "(status NOT IN ('queued','running') AND active_slot IS NULL)",
            name="ck_import_active_slot",
        ),
    )
    op.create_index("ix_import_jobs_request", "import_jobs", ["request_key"])
    op.create_table(
        "import_batches",
        sa.Column("job_id", sa.String(36), sa.ForeignKey("import_jobs.job_id"), primary_key=True),
        sa.Column("batch_index", sa.Integer(), primary_key=True),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("bar_count", sa.Integer(), nullable=False),
        sa.Column("first_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("batch_index >= 0 AND bar_count > 0", name="ck_import_batch_counts"),
    )


def downgrade() -> None:
    op.drop_table("import_batches")
    op.drop_index("ix_import_jobs_request", table_name="import_jobs")
    op.drop_table("import_jobs")
