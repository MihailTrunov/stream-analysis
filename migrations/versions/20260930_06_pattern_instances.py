"""Persist independently identified pattern occurrences and lifecycle evidence.

Revision ID: 20260930_06
Revises: 20260929_05
"""

import sqlalchemy as sa

from alembic import op

revision = "20260930_06"
down_revision = "20260929_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pattern_instances",
        sa.Column("instance_id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("run_snapshots.run_id"), nullable=False),
        sa.Column("instance_semantic_key", sa.String(64), nullable=False),
        sa.Column("dataset_revision_id", sa.String(200), nullable=False),
        sa.Column("instrument_id", sa.String(200), nullable=False),
        sa.Column("timeframe", sa.String(10), nullable=False),
        sa.Column("detection_config_hash", sa.String(64), nullable=False),
        sa.Column("calendar_version", sa.String(200), nullable=False),
        sa.Column("build_id", sa.String(200), nullable=False),
        sa.Column("binding_fingerprint", sa.String(64), nullable=False),
        sa.Column("pattern_id", sa.String(200), nullable=False),
        sa.Column("pattern_version", sa.String(100), nullable=False),
        sa.Column("definition_fingerprint", sa.String(64), nullable=False),
        sa.Column("occurrence_event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurrence_detection_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurrence_ordinal", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(100), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("last_bar_time", sa.DateTime(timezone=True)),
        sa.Column("context_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "run_id", "instance_semantic_key", name="uq_pattern_instance_run_semantic"
        ),
        sa.CheckConstraint("revision >= 0 AND occurrence_ordinal >= 0", name="ck_pattern_progress"),
        sa.CheckConstraint(
            "occurrence_event_time <= occurrence_detection_time", name="ck_pattern_occurrence_time"
        ),
    )
    op.create_index("ix_pattern_instances_run", "pattern_instances", ["run_id"])
    op.create_table(
        "pattern_instance_transitions",
        sa.Column(
            "instance_id", sa.String(36), sa.ForeignKey("pattern_instances.instance_id"),
            primary_key=True,
        ),
        sa.Column("sequence", sa.Integer(), primary_key=True),
        sa.Column("event_semantic_ref", sa.String(64), nullable=False),
        sa.Column("bar_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detection_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("from_state", sa.String(100), nullable=False),
        sa.Column("to_state", sa.String(100), nullable=False),
        sa.Column("trigger_id", sa.String(200), nullable=False),
        sa.CheckConstraint("sequence >= 0", name="ck_pattern_transition_sequence"),
        sa.CheckConstraint("event_time <= detection_time", name="ck_pattern_transition_time"),
        sa.CheckConstraint("bar_time = detection_time", name="ck_pattern_transition_bar_time"),
    )


def downgrade() -> None:
    op.drop_table("pattern_instance_transitions")
    op.drop_index("ix_pattern_instances_run", table_name="pattern_instances")
    op.drop_table("pattern_instances")
