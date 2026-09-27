"""Persist instrument identity and immutable dataset revision metadata.

Revision ID: 20260927_01
Revises: 20260926_01
"""

import sqlalchemy as sa

from alembic import op

revision = "20260927_01"
down_revision = "20260926_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("instrument_id", sa.String(200), primary_key=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("calendar_id", sa.String(200), nullable=False),
        sa.Column("price_precision", sa.Integer(), nullable=False),
        sa.Column("point_size", sa.Text(), nullable=False),
        sa.CheckConstraint("price_precision >= 0", name="ck_instrument_price_precision"),
        sa.CheckConstraint("length(point_size) > 0", name="ck_instrument_point_size"),
    )
    op.create_table(
        "provider_symbol_mappings",
        sa.Column(
            "instrument_id",
            sa.String(200),
            sa.ForeignKey("instruments.instrument_id"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(200), nullable=False),
        sa.Column("provider_key", sa.String(200), nullable=False),
        sa.Column("environment", sa.String(200)),
        sa.Column("environment_key", sa.String(200), nullable=False),
        sa.Column("symbol", sa.String(200), nullable=False),
        sa.Column("symbol_key", sa.String(200), nullable=False),
        sa.UniqueConstraint(
            "instrument_id", "provider_key", "environment_key", name="uq_instrument_provider_env"
        ),
        sa.UniqueConstraint(
            "provider_key", "environment_key", "symbol_key", name="uq_provider_symbol_identity"
        ),
        sa.CheckConstraint("length(provider_key) > 0", name="ck_provider_key"),
        sa.CheckConstraint("length(symbol_key) > 0", name="ck_symbol_key"),
    )
    op.create_index("ix_provider_mapping_instrument", "provider_symbol_mappings", ["instrument_id"])
    op.create_table(
        "dataset_revisions",
        sa.Column("dataset_revision_id", sa.String(200), primary_key=True),
        sa.Column("dataset_id", sa.String(200), nullable=False),
        sa.Column("source_id", sa.String(200), nullable=False),
        sa.Column("provider", sa.String(200), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("normalization_version", sa.String(100), nullable=False),
        sa.Column("calendar_version", sa.String(200), nullable=False),
        sa.Column("manifest_format_version", sa.String(100), nullable=False),
        sa.Column("manifest_ref", sa.Text(), nullable=False),
        sa.CheckConstraint("length(dataset_id) > 0", name="ck_dataset_id"),
        sa.CheckConstraint("length(source_id) > 0", name="ck_dataset_source"),
    )
    op.create_index("ix_dataset_revisions_dataset", "dataset_revisions", ["dataset_id"])
    op.create_table(
        "dataset_memberships",
        sa.Column(
            "dataset_revision_id",
            sa.String(200),
            sa.ForeignKey("dataset_revisions.dataset_revision_id"),
            nullable=False,
        ),
        sa.Column(
            "instrument_id",
            sa.String(200),
            sa.ForeignKey("instruments.instrument_id"),
            nullable=False,
        ),
        sa.Column("timeframe", sa.String(10), nullable=False),
        sa.Column("range_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("range_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bar_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "dataset_revision_id", "instrument_id", "timeframe", name="uq_dataset_membership"
        ),
        sa.CheckConstraint("range_end > range_start", name="ck_dataset_membership_range"),
        sa.CheckConstraint("bar_count >= 0", name="ck_dataset_membership_count"),
        sa.CheckConstraint(
            "timeframe IN ('1m', '5m', '15m', '1h', '1d')", name="ck_dataset_timeframe"
        ),
    )
    op.create_index(
        "ix_dataset_membership_lookup",
        "dataset_memberships",
        ["instrument_id", "timeframe", "range_start", "range_end"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE FUNCTION reject_market_metadata_mutation() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION 'market metadata is immutable'; "
            "END; $$"
        )
        for table in (
            "instruments",
            "provider_symbol_mappings",
            "dataset_revisions",
            "dataset_memberships",
        ):
            op.execute(
                f"CREATE TRIGGER reject_{table}_mutation "
                f"BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_market_metadata_mutation()"
            )


def downgrade() -> None:
    op.drop_index("ix_dataset_membership_lookup", table_name="dataset_memberships")
    op.drop_table("dataset_memberships")
    op.drop_index("ix_dataset_revisions_dataset", table_name="dataset_revisions")
    op.drop_table("dataset_revisions")
    op.drop_index("ix_provider_mapping_instrument", table_name="provider_symbol_mappings")
    op.drop_table("provider_symbol_mappings")
    op.drop_table("instruments")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION reject_market_metadata_mutation()")
