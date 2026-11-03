"""Pin multi-timeframe analysis session lineage.

Revision ID: 20261103_11
Revises: 20261002_10
"""

import sqlalchemy as sa

from alembic import op

revision = "20261103_11"
down_revision = "20261002_10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analysis_session_revisions",
        sa.Column("session_id", sa.String(36), primary_key=True),
        sa.Column("manifest_json", sa.Text(), nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("analysis_session_revisions")
