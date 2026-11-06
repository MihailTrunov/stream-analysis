"""Store versioned downstream outcome observations without changing detector truth."""

import sqlalchemy as sa

from alembic import op

revision = "20261106_17"
down_revision = "20261106_16"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outcome_observations",
        sa.Column("observation_id", sa.String(36), primary_key=True),
        sa.Column(
            "event_id",
            sa.String(36),
            sa.ForeignKey("pattern_instance_transitions.event_id"),
            nullable=False,
        ),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("run_snapshots.run_id"), nullable=False),
        sa.Column(
            "context_snapshot_id",
            sa.String(36),
            sa.ForeignKey("detection_context_snapshots.snapshot_id"),
            nullable=False,
        ),
        sa.Column("semantic_key", sa.String(64), nullable=False),
        sa.Column("definition_json", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("event_id", "semantic_key", name="uq_outcome_event_semantic"),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text("""
            CREATE FUNCTION reject_outcome_mutation() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'outcome observations are immutable'; END;
            $$ LANGUAGE plpgsql
        """)
        )
        op.execute(
            sa.text("""
            CREATE TRIGGER outcome_observations_immutable BEFORE UPDATE OR DELETE
            ON outcome_observations FOR EACH ROW EXECUTE FUNCTION reject_outcome_mutation()
        """)
        )
    elif op.get_bind().dialect.name == "sqlite":
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                sa.text(
                    f"CREATE TRIGGER outcome_observations_immutable_{operation.lower()} "
                    f"BEFORE {operation} ON outcome_observations BEGIN "
                    "SELECT RAISE(ABORT, 'outcome observations are immutable'); END"
                )
            )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM outcome_observations")):
        raise RuntimeError("outcome observations must be retained; restore a pre-outcome backup")
    op.drop_table("outcome_observations")
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("DROP FUNCTION reject_outcome_mutation()"))
