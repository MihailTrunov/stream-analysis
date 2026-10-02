"""Separate audited manual-validation research annotations.

Revision ID: 20261002_10
Revises: 20260930_09
"""

import sqlalchemy as sa

from alembic import op

revision = "20261002_10"
down_revision = "20260930_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "validation_annotations",
        sa.Column("annotation_id", sa.String(36), primary_key=True),
        sa.Column("target_kind", sa.String(20), nullable=False),
        sa.Column(
            "event_id", sa.String(36), sa.ForeignKey("pattern_instance_transitions.event_id")
        ),
        sa.Column("instance_id", sa.String(36), sa.ForeignKey("pattern_instances.instance_id")),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("run_snapshots.run_id")),
        sa.Column("dataset_revision_id", sa.String(200), nullable=False),
        sa.Column(
            "missed_dataset_revision_id",
            sa.String(200),
            sa.ForeignKey("dataset_revisions.dataset_revision_id"),
        ),
        sa.Column("instrument_id", sa.String(200), nullable=False),
        sa.Column("timeframe", sa.String(10), nullable=False),
        sa.Column("pattern_id", sa.String(200), nullable=False),
        sa.Column("pattern_version", sa.String(100), nullable=False),
        sa.Column("detection_config_hash", sa.String(64)),
        sa.Column("calendar_version", sa.String(200)),
        sa.Column("build_id", sa.String(200)),
        sa.Column("interval_start", sa.DateTime(timezone=True)),
        sa.Column("interval_end", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(target_kind = 'event' AND event_id IS NOT NULL AND instance_id IS NOT NULL "
            "AND run_id IS NOT NULL AND missed_dataset_revision_id IS NULL "
            "AND interval_start IS NULL AND interval_end IS NULL) OR "
            "(target_kind = 'instance' AND event_id IS NULL AND instance_id IS NOT NULL "
            "AND run_id IS NOT NULL AND missed_dataset_revision_id IS NULL "
            "AND interval_start IS NULL AND interval_end IS NULL) OR "
            "(target_kind = 'missed_pattern' AND event_id IS NULL AND instance_id IS NULL "
            "AND run_id IS NULL AND missed_dataset_revision_id = dataset_revision_id "
            "AND interval_start IS NOT NULL AND interval_end IS NOT NULL "
            "AND interval_end > interval_start)",
            name="ck_validation_annotation_target",
        ),
    )
    op.create_index("ix_validation_annotations_event", "validation_annotations", ["event_id"])
    op.create_index("ix_validation_annotations_instance", "validation_annotations", ["instance_id"])
    op.create_index(
        "ix_validation_annotations_dataset", "validation_annotations", ["dataset_revision_id"]
    )
    op.create_table(
        "validation_annotation_revisions",
        sa.Column(
            "annotation_id",
            sa.String(36),
            sa.ForeignKey("validation_annotations.annotation_id"),
            primary_key=True,
        ),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("label", sa.String(30), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("reviewer_id", sa.String(200)),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_annotation_revision_positive"),
    )
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        op.execute(
            sa.text(
                "CREATE FUNCTION reject_validation_annotation_mutation() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN "
                "RAISE EXCEPTION 'validation annotation audit rows are immutable'; END; $$"
            )
        )
        for table in ("validation_annotations", "validation_annotation_revisions"):
            op.execute(
                sa.text(
                    f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE "
                    f"ON {table} FOR EACH ROW EXECUTE FUNCTION "
                    "reject_validation_annotation_mutation()"
                )
            )
    elif connection.dialect.name == "sqlite":
        for table in ("validation_annotations", "validation_annotation_revisions"):
            for operation in ("UPDATE", "DELETE"):
                op.execute(
                    sa.text(
                        f"CREATE TRIGGER {table}_immutable_{operation.lower()} "
                        f"BEFORE {operation} ON {table} "
                        "BEGIN SELECT RAISE(ABORT, "
                        "'validation annotation audit rows are immutable'); END"
                    )
                )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        for table in ("validation_annotations", "validation_annotation_revisions"):
            op.execute(sa.text(f"DROP TRIGGER {table}_immutable ON {table}"))
        op.execute(sa.text("DROP FUNCTION reject_validation_annotation_mutation()"))
    elif connection.dialect.name == "sqlite":
        for table in ("validation_annotations", "validation_annotation_revisions"):
            for operation in ("UPDATE", "DELETE"):
                op.execute(sa.text(f"DROP TRIGGER {table}_immutable_{operation.lower()}"))
    op.drop_table("validation_annotation_revisions")
    op.drop_index("ix_validation_annotations_dataset", table_name="validation_annotations")
    op.drop_index("ix_validation_annotations_instance", table_name="validation_annotations")
    op.drop_index("ix_validation_annotations_event", table_name="validation_annotations")
    op.drop_table("validation_annotations")
