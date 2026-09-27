"""Record the hash algorithm and payload version on run snapshots.

Revision ID: 20260927_02
Revises: 20260927_01
"""

import sqlalchemy as sa

from alembic import op

revision = "20260927_02"
down_revision = "20260927_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "run_snapshots",
        sa.Column(
            "detection_hash_algorithm", sa.String(20), nullable=False, server_default="sha256"
        ),
    )
    op.add_column(
        "run_snapshots",
        sa.Column(
            "detection_hash_version",
            sa.String(100),
            nullable=False,
            server_default="detection-config-v1",
        ),
    )
    op.add_column("run_snapshots", sa.Column("evaluation_hash_algorithm", sa.String(20)))
    op.add_column("run_snapshots", sa.Column("evaluation_hash_version", sa.String(100)))
    op.execute(
        "UPDATE run_snapshots SET evaluation_hash_algorithm = 'sha256', "
        "evaluation_hash_version = 'evaluation-plan-v1' WHERE run_kind = 'evaluation'"
    )


def downgrade() -> None:
    op.drop_column("run_snapshots", "evaluation_hash_version")
    op.drop_column("run_snapshots", "evaluation_hash_algorithm")
    op.drop_column("run_snapshots", "detection_hash_version")
    op.drop_column("run_snapshots", "detection_hash_algorithm")
