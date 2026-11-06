"""Pin evaluation inputs and record source-context-qualified EventSet membership."""

import sqlalchemy as sa

from alembic import op

revision = "20261106_18"
down_revision = "20261106_17"
branch_labels = None
depends_on = None

IMMUTABLE = ("evaluation_inputs", "evaluation_streams", "evaluation_event_sets")


def upgrade() -> None:
    op.create_table(
        "evaluation_inputs",
        sa.Column("evaluation_id", sa.String(36), primary_key=True),
        sa.Column("semantic_key", sa.String(64), nullable=False),
        sa.Column("event_set_id", sa.String(64), nullable=False),
        sa.Column("manifest_json", sa.Text(), nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
    )
    op.create_table(
        "evaluation_streams",
        sa.Column(
            "evaluation_id",
            sa.String(36),
            sa.ForeignKey("evaluation_inputs.evaluation_id"),
            primary_key=True,
        ),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("run_snapshots.run_id"), primary_key=True),
        sa.Column("stream_order", sa.Integer(), nullable=False),
        sa.UniqueConstraint("run_id", name="uq_evaluation_source_run"),
        sa.UniqueConstraint("evaluation_id", "stream_order", name="uq_evaluation_stream_order"),
    )
    op.create_table(
        "evaluation_runs",
        sa.Column(
            "evaluation_id",
            sa.String(36),
            sa.ForeignKey("evaluation_inputs.evaluation_id"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("cursor_index", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("owner_token", sa.String(36)),
        sa.Column("active_slot", sa.Integer(), unique=True),
        sa.Column("failure_reason", sa.Text()),
        sa.CheckConstraint(
            "status IN ('CREATED','RUNNING','DETECTION_COMPLETED',"
            "'COMPLETED','FAILED','CANCELLED')",
            name="ck_evaluation_status",
        ),
        sa.CheckConstraint("revision >= 0 AND cursor_index >= -1", name="ck_evaluation_progress"),
        sa.CheckConstraint(
            "(status = 'RUNNING' AND active_slot IS NOT NULL AND active_slot = 1 "
            "AND owner_token IS NOT NULL) OR (status <> 'RUNNING' "
            "AND active_slot IS NULL AND owner_token IS NULL)",
            name="ck_evaluation_active_owner",
        ),
        sa.CheckConstraint(
            "(status = 'COMPLETED' AND completed_at IS NOT NULL) OR "
            "(status <> 'COMPLETED' AND completed_at IS NULL)",
            name="ck_evaluation_completed_time",
        ),
    )
    op.create_table(
        "evaluation_event_sets",
        sa.Column(
            "evaluation_id",
            sa.String(36),
            sa.ForeignKey("evaluation_inputs.evaluation_id"),
            primary_key=True,
        ),
        sa.Column(
            "event_id",
            sa.String(36),
            sa.ForeignKey("pattern_instance_transitions.event_id"),
            primary_key=True,
        ),
        sa.Column(
            "context_snapshot_id",
            sa.String(36),
            sa.ForeignKey("detection_context_snapshots.snapshot_id"),
            nullable=False,
        ),
        sa.Column(
            "source_run_id", sa.String(36), sa.ForeignKey("run_snapshots.run_id"), nullable=False
        ),
        sa.Column("event_semantic_ref", sa.String(64), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("emission_order", sa.Integer(), nullable=False),
        sa.Column("within_bar_ordinal", sa.Integer(), nullable=False),
        sa.Column("eligible", sa.Integer(), nullable=False),
        sa.Column("exclusion_reason", sa.Text()),
        sa.UniqueConstraint("evaluation_id", "emission_order", name="uq_evaluation_event_order"),
        sa.CheckConstraint(
            "emission_order >= 0 AND within_bar_ordinal >= 0 AND eligible IN (0,1)",
            name="ck_evaluation_event_member",
        ),
        sa.CheckConstraint(
            "(eligible = 1 AND exclusion_reason IS NULL) OR "
            "(eligible = 0 AND exclusion_reason IS NOT NULL)",
            name="ck_evaluation_exclusion_reason",
        ),
    )
    op.create_table(
        "evaluation_stream_progress",
        sa.Column("evaluation_id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), primary_key=True),
        sa.Column("processed_bars", sa.Integer(), nullable=False),
        sa.Column("last_available_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["evaluation_id", "run_id"],
            ["evaluation_streams.evaluation_id", "evaluation_streams.run_id"],
        ),
        sa.CheckConstraint("processed_bars >= 0", name="ck_evaluation_stream_progress"),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text("""
            CREATE FUNCTION reject_evaluation_input_mutation() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'evaluation inputs and EventSets are immutable'; END;
            $$ LANGUAGE plpgsql
        """)
        )
        for table in IMMUTABLE:
            op.execute(
                sa.text(
                    f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
                    "FOR EACH ROW EXECUTE FUNCTION reject_evaluation_input_mutation()"
                )
            )
    elif op.get_bind().dialect.name == "sqlite":
        for table in IMMUTABLE:
            for operation in ("UPDATE", "DELETE"):
                op.execute(
                    sa.text(
                        f"CREATE TRIGGER {table}_immutable_{operation.lower()} BEFORE {operation} "
                        f"ON {table} BEGIN SELECT RAISE(ABORT, "
                        "'evaluation inputs and EventSets are immutable'); END"
                    )
                )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM evaluation_inputs")):
        raise RuntimeError("evaluation lineage must be retained; restore a pre-evaluation backup")
    for table in (
        "evaluation_event_sets",
        "evaluation_runs",
        "evaluation_stream_progress",
        "evaluation_streams",
        "evaluation_inputs",
    ):
        op.drop_table(table)
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("DROP FUNCTION reject_evaluation_input_mutation()"))
