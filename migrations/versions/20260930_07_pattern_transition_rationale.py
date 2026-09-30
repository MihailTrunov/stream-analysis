"""Persist validated transition rationale evidence with each transition row.

Revision ID: 20260930_07
Revises: 20260930_06
"""

import sqlalchemy as sa

from alembic import op

revision = "20260930_07"
down_revision = "20260930_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pattern_instance_transitions",
        sa.Column("rationale_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pattern_instance_transitions", "rationale_json")
