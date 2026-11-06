"""Attribute live analytical runs to a pinned bootstrap and captured close receipts."""

import sqlalchemy as sa

from alembic import op

revision = "20261105_15"
down_revision = "20261105_14"
branch_labels = None
depends_on = None


def _restore_sqlite_annotation_guards() -> None:
    if op.get_bind().dialect.name == "sqlite":
        for operation in ("UPDATE", "DELETE"):
            op.execute(sa.text(
                f"CREATE TRIGGER validation_annotations_immutable_{operation.lower()} "
                f"BEFORE {operation} ON validation_annotations BEGIN "
                "SELECT RAISE(ABORT, 'validation_annotations is immutable'); END"
            ))


def upgrade() -> None:
    op.create_table(
        "live_analysis_inputs",
        sa.Column("input_id", sa.String(200), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("analysis_session_revisions.session_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("manifest_json", sa.Text(), nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
    )
    op.create_table(
        "live_analysis_closes",
        sa.Column(
            "input_id",
            sa.String(200),
            sa.ForeignKey("live_analysis_inputs.input_id"),
            primary_key=True,
        ),
        sa.Column("timestamp", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("feed_key", sa.String(64), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["feed_key", "timestamp"], ["live_candles.feed_key", "live_candles.timestamp"]
        ),
    )
    op.create_table(
        "live_analysis_advances",
        sa.Column(
            "input_id",
            sa.String(200),
            sa.ForeignKey("live_analysis_inputs.input_id"),
            primary_key=True,
        ),
        sa.Column("available_at", sa.DateTime(timezone=True), primary_key=True),
    )

    with op.batch_alter_table("validation_annotations") as batch:
        batch.add_column(sa.Column("missed_live_input_id", sa.String(200)))
        batch.create_foreign_key(
            "fk_annotation_live_input",
            "live_analysis_inputs",
            ["missed_live_input_id"],
            ["input_id"],
        )
        batch.drop_constraint("ck_validation_annotation_target", type_="check")
        batch.create_check_constraint(
            "ck_validation_annotation_target",
            (
                "(target_kind = 'event' AND event_id IS NOT NULL AND instance_id IS NOT NULL "
                "AND run_id IS NOT NULL AND missed_dataset_revision_id IS NULL "
                "AND missed_live_input_id IS NULL AND interval_start IS NULL "
                "AND interval_end IS NULL) OR "
                "(target_kind = 'instance' AND event_id IS NULL AND instance_id IS NOT NULL "
                "AND run_id IS NOT NULL AND missed_dataset_revision_id IS NULL "
                "AND missed_live_input_id IS NULL AND interval_start IS NULL "
                "AND interval_end IS NULL) OR "
                "(target_kind = 'missed_pattern' AND event_id IS NULL AND instance_id IS NULL "
                "AND run_id IS NULL AND ((missed_dataset_revision_id IS NOT NULL "
                "AND missed_dataset_revision_id = dataset_revision_id "
                "AND missed_live_input_id IS NULL) OR (missed_live_input_id IS NOT NULL "
                "AND missed_live_input_id = dataset_revision_id "
                "AND missed_dataset_revision_id IS NULL)) "
                "AND interval_start IS NOT NULL AND interval_end IS NOT NULL "
                "AND interval_end > interval_start)"
            ),
        )
    _restore_sqlite_annotation_guards()


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM live_analysis_inputs")):
        raise RuntimeError(
            "live analytical input lineage must be retained; restore a pre-live backup"
        )
    with op.batch_alter_table("validation_annotations") as batch:
        batch.drop_constraint("fk_annotation_live_input", type_="foreignkey")
        batch.drop_constraint("ck_validation_annotation_target", type_="check")
        batch.drop_column("missed_live_input_id")
        batch.create_check_constraint(
            "ck_validation_annotation_target",
            (
                "(target_kind = 'event' AND event_id IS NOT NULL AND instance_id IS NOT NULL "
                "AND run_id IS NOT NULL AND missed_dataset_revision_id IS NULL "
                "AND interval_start IS NULL AND interval_end IS NULL) OR "
                "(target_kind = 'instance' AND event_id IS NULL AND instance_id IS NOT NULL "
                "AND run_id IS NOT NULL AND missed_dataset_revision_id IS NULL "
                "AND interval_start IS NULL AND interval_end IS NULL) OR "
                "(target_kind = 'missed_pattern' AND event_id IS NULL AND instance_id IS NULL "
                "AND run_id IS NULL AND missed_dataset_revision_id = dataset_revision_id "
                "AND interval_start IS NOT NULL AND interval_end IS NOT NULL "
                "AND interval_end > interval_start)"
            ),
        )
    _restore_sqlite_annotation_guards()
    op.drop_table("live_analysis_advances")
    op.drop_table("live_analysis_closes")
    op.drop_table("live_analysis_inputs")
