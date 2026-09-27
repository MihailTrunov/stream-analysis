"""Persist immutable per-membership dataset content and acquisition lineage.

Revision ID: 20260927_03
Revises: 20260927_02
"""

import sqlalchemy as sa

from alembic import op

revision = "20260927_03"
down_revision = "20260927_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dataset_content_lineage",
        sa.Column("dataset_revision_id", sa.String(200), primary_key=True),
        sa.Column("instrument_id", sa.String(200), primary_key=True),
        sa.Column("timeframe", sa.String(10), primary_key=True),
        sa.Column("source_dataset_id", sa.String(200), nullable=False),
        sa.Column("requested_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actual_start", sa.DateTime(timezone=True)),
        sa.Column("actual_end", sa.DateTime(timezone=True)),
        sa.Column("bar_count", sa.Integer(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("validation_status", sa.String(20), nullable=False),
        sa.Column("provider_request_json", sa.Text(), nullable=False),
        sa.Column("source_checksum", sa.String(64), nullable=False),
        sa.Column("canonical_checksum", sa.String(64), nullable=False),
        sa.Column("checksum_version", sa.String(100), nullable=False),
        sa.Column("dataset_format_version", sa.String(100), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_revision_id", "instrument_id", "timeframe"],
            [
                "dataset_memberships.dataset_revision_id",
                "dataset_memberships.instrument_id",
                "dataset_memberships.timeframe",
            ],
            name="fk_lineage_membership",
        ),
        sa.CheckConstraint("requested_end >= requested_start", name="ck_lineage_requested_range"),
        sa.CheckConstraint("bar_count >= 0", name="ck_lineage_bar_count"),
        sa.CheckConstraint(
            "(bar_count = 0 AND actual_start IS NULL AND actual_end IS NULL) OR "
            "(bar_count > 0 AND actual_start IS NOT NULL AND actual_end IS NOT NULL "
            "AND actual_start < actual_end)",
            name="ck_lineage_actual_range",
        ),
        sa.CheckConstraint(
            "validation_status IN ('pass', 'warning', 'fail')",
            name="ck_lineage_validation_status",
        ),
    )
    op.create_index(
        "ix_lineage_source_dataset", "dataset_content_lineage", ["source_dataset_id"]
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE TRIGGER reject_dataset_content_lineage_mutation "
            "BEFORE UPDATE OR DELETE ON dataset_content_lineage "
            "FOR EACH ROW EXECUTE FUNCTION reject_market_metadata_mutation()"
        )


def downgrade() -> None:
    op.drop_index("ix_lineage_source_dataset", table_name="dataset_content_lineage")
    op.drop_table("dataset_content_lineage")
