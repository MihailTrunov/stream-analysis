"""Persist immutable replay and evaluation configuration snapshots.

Revision ID: 20260926_01
Revises:
"""

import sqlalchemy as sa

from alembic import op

revision = "20260926_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_snapshots",
        sa.Column("run_id", sa.String(36), primary_key=True),
        sa.Column("run_kind", sa.String(10), nullable=False),
        sa.Column("dataset_revision_id", sa.String(200), nullable=False),
        sa.Column("preset_id", sa.String(200)),
        sa.Column("preset_revision", sa.Integer()),
        sa.Column("calendar_version", sa.String(200), nullable=False),
        sa.Column("build_id", sa.String(200), nullable=False),
        sa.Column("config_schema_version", sa.String(100), nullable=False),
        sa.Column("detection_config_hash", sa.String(64), nullable=False),
        sa.Column("detection_config_json", sa.Text(), nullable=False),
        sa.Column("evaluation_plan_hash", sa.String(64)),
        sa.Column("evaluation_plan_json", sa.Text()),
        sa.CheckConstraint("run_kind IN ('replay', 'evaluation')", name="ck_run_kind"),
        sa.CheckConstraint(
            "(run_kind = 'replay' AND evaluation_plan_json IS NULL "
            "AND evaluation_plan_hash IS NULL) OR "
            "(run_kind = 'evaluation' AND evaluation_plan_json IS NOT NULL "
            "AND evaluation_plan_hash IS NOT NULL)",
            name="ck_run_plan",
        ),
        sa.CheckConstraint(
            "(preset_id IS NULL AND preset_revision IS NULL) OR "
            "(preset_id IS NOT NULL AND preset_revision > 0)",
            name="ck_run_preset",
        ),
    )


def downgrade() -> None:
    op.drop_table("run_snapshots")
