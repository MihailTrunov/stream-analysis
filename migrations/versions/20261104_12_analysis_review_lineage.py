"""Pin analytical/source lineage for missed-pattern audit records.

Revision ID: 20261104_12
Revises: 20261103_11
"""

import sqlalchemy as sa

from alembic import op

revision = "20261104_12"
down_revision = "20261103_11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analysis_annotation_lineage",
        sa.Column(
            "annotation_id",
            sa.String(36),
            sa.ForeignKey("validation_annotations.annotation_id"),
            primary_key=True,
        ),
        sa.Column("lineage_json", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("analysis_annotation_lineage")
