"""Record explicit Git/build capture state on immutable run snapshots.

Revision ID: 20260930_09
Revises: 20260930_08
"""

import sqlalchemy as sa

from alembic import op

revision = "20260930_09"
down_revision = "20260930_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("run_snapshots", sa.Column("code_revision", sa.String(64)))
    op.add_column("run_snapshots", sa.Column("code_dirty", sa.Boolean()))
    op.add_column(
        "run_snapshots",
        sa.Column(
            "code_capture_status", sa.String(20), nullable=False,
            server_default="unavailable",
        ),
    )
    op.add_column(
        "run_snapshots",
        sa.Column(
            "code_capture_source", sa.String(100), nullable=False,
            server_default="legacy-unavailable",
        ),
    )
    with op.batch_alter_table("run_snapshots") as batch:
        batch.create_check_constraint(
            "ck_run_code_version",
            "(code_capture_status = 'unavailable' AND code_revision IS NULL "
            "AND code_dirty IS NULL) OR "
            "(code_capture_status = 'available' AND code_revision IS NOT NULL)",
        )


def downgrade() -> None:
    with op.batch_alter_table("run_snapshots") as batch:
        batch.drop_constraint("ck_run_code_version", type_="check")
        batch.drop_column("code_capture_source")
        batch.drop_column("code_capture_status")
        batch.drop_column("code_dirty")
        batch.drop_column("code_revision")
