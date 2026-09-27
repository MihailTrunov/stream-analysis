"""Persist ReplayRun lifecycle separately from immutable run snapshots.

Revision ID: 20260927_04
Revises: 20260927_03
"""

import sqlalchemy as sa

from alembic import op

revision = "20260927_04"
down_revision = "20260927_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "replay_runs",
        sa.Column(
            "run_id", sa.String(36), sa.ForeignKey("run_snapshots.run_id"), primary_key=True
        ),
        sa.Column("selected_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("selected_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("cursor_index", sa.Integer(), nullable=False),
        sa.Column("source_bar_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("failure_reason", sa.Text()),
        sa.CheckConstraint("selected_end > selected_start", name="ck_replay_selected_range"),
        sa.CheckConstraint("cursor_index >= -1", name="ck_replay_cursor"),
        sa.CheckConstraint("source_bar_count > 0", name="ck_replay_bar_count"),
        sa.CheckConstraint("cursor_index < source_bar_count", name="ck_replay_cursor_bound"),
        sa.CheckConstraint(
            "status IN ('created','running','paused','completed','failed','aborted')",
            name="ck_replay_status",
        ),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE FUNCTION reject_run_snapshot_mutation() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION 'run snapshot is immutable'; "
            "END; $$"
        )
        op.execute(
            "CREATE TRIGGER reject_run_snapshot_mutation "
            "BEFORE UPDATE OR DELETE ON run_snapshots "
            "FOR EACH ROW EXECUTE FUNCTION reject_run_snapshot_mutation()"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER reject_run_snapshot_mutation ON run_snapshots")
        op.execute("DROP FUNCTION reject_run_snapshot_mutation()")
    op.drop_table("replay_runs")
