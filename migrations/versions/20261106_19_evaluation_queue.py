"""Persist serial evaluation queue and worker leases."""

import sqlalchemy as sa

from alembic import op

revision = "20261106_19"
down_revision = "20261106_18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evaluation_queue",
        sa.Column("singleton", sa.Integer(), primary_key=True),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("next_position", sa.Integer(), nullable=False),
        sa.CheckConstraint("singleton = 1", name="ck_evaluation_queue_singleton"),
        sa.CheckConstraint(
            "revision >= 0 AND next_position >= 0", name="ck_evaluation_queue_progress"
        ),
    )
    op.execute("INSERT INTO evaluation_queue VALUES (1, false, 0, 0)")
    op.create_table(
        "evaluation_jobs",
        sa.Column("job_id", sa.String(36), primary_key=True),
        sa.Column(
            "evaluation_id",
            sa.String(36),
            sa.ForeignKey("evaluation_inputs.evaluation_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("position", sa.Integer(), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("active_slot", sa.Integer(), unique=True),
        sa.Column("stop_requested", sa.Boolean(), nullable=False),
        sa.Column("failure_reason", sa.Text()),
        sa.CheckConstraint(
            "status IN ('QUEUED','RUNNING','COMPLETED','CANCELLED','FAILED')",
            name="ck_evaluation_job_status",
        ),
        sa.CheckConstraint("position >= 0", name="ck_evaluation_job_position"),
        sa.CheckConstraint(
            "(status = 'RUNNING' AND active_slot IS NOT NULL AND active_slot = 1 "
            "AND lease_token IS NOT NULL) OR (status <> 'RUNNING' "
            "AND active_slot IS NULL AND lease_token IS NULL)",
            name="ck_evaluation_job_owner",
        ),
        sa.CheckConstraint(
            "(status IN ('COMPLETED','CANCELLED','FAILED') AND finished_at IS NOT NULL) "
            "OR (status IN ('QUEUED','RUNNING') AND finished_at IS NULL)",
            name="ck_evaluation_job_finished",
        ),
    )
    if op.get_bind().dialect.name == "sqlite":
        op.execute("""CREATE TRIGGER evaluation_job_frozen_link BEFORE UPDATE ON evaluation_jobs
            WHEN NEW.job_id <> OLD.job_id OR NEW.evaluation_id <> OLD.evaluation_id
                 OR NEW.created_at <> OLD.created_at
            BEGIN SELECT RAISE(ABORT, 'evaluation job inputs are immutable'); END""")
        op.execute("""CREATE TRIGGER evaluation_job_retained BEFORE DELETE ON evaluation_jobs
            BEGIN SELECT RAISE(ABORT, 'evaluation job history must be retained'); END""")
    else:
        op.execute("""CREATE FUNCTION guard_evaluation_job() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'evaluation job history must be retained';
                END IF;
                IF NEW.job_id IS DISTINCT FROM OLD.job_id
                   OR NEW.evaluation_id IS DISTINCT FROM OLD.evaluation_id
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                    RAISE EXCEPTION 'evaluation job inputs are immutable';
                END IF;
                RETURN NEW;
            END; $$ LANGUAGE plpgsql""")
        op.execute("""CREATE TRIGGER evaluation_job_guard BEFORE UPDATE OR DELETE ON evaluation_jobs
            FOR EACH ROW EXECUTE FUNCTION guard_evaluation_job()""")


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM evaluation_jobs")).scalar_one():
        raise RuntimeError("evaluation job history must be retained; restore a pre-queue backup")
    op.drop_table("evaluation_jobs")
    op.drop_table("evaluation_queue")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION guard_evaluation_job()")
