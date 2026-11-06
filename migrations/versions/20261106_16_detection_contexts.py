"""Retain immutable detection-time context separately from occurrence progress."""

import sqlalchemy as sa

from alembic import op

revision = "20261106_16"
down_revision = "20261105_15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "detection_context_snapshots",
        sa.Column("snapshot_id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36),
                  sa.ForeignKey("pattern_instance_transitions.event_id"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("run_snapshots.run_id"), nullable=False),
        sa.Column("context_schema_version", sa.String(100), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("event_id", "context_schema_version", name="uq_event_context_schema"),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("""
            CREATE FUNCTION reject_detection_context_mutation() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'detection context is immutable'; END;
            $$ LANGUAGE plpgsql
        """))
        op.execute(sa.text("""
            CREATE TRIGGER detection_context_immutable BEFORE UPDATE OR DELETE
            ON detection_context_snapshots FOR EACH ROW
            EXECUTE FUNCTION reject_detection_context_mutation()
        """))
    elif op.get_bind().dialect.name == "sqlite":
        for operation in ("UPDATE", "DELETE"):
            op.execute(sa.text(
                f"CREATE TRIGGER detection_context_immutable_{operation.lower()} "
                f"BEFORE {operation} ON detection_context_snapshots BEGIN "
                "SELECT RAISE(ABORT, 'detection context is immutable'); END"
            ))


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM detection_context_snapshots")):
        raise RuntimeError("detection-time context must be retained; restore a pre-capture backup")
    op.drop_table("detection_context_snapshots")
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("DROP FUNCTION reject_detection_context_mutation()"))
