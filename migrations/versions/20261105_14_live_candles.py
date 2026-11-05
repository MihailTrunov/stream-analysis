"""Journal completed live minutes and seal hourly immutable publications."""

import sqlalchemy as sa

from alembic import op

revision = "20261105_14"
down_revision = "20261104_13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "live_feeds",
        sa.Column("feed_key", sa.String(64), primary_key=True),
        sa.Column("feed_json", sa.Text(), nullable=False),
    )
    op.create_table(
        "live_candles",
        sa.Column("feed_key", sa.String(64), sa.ForeignKey("live_feeds.feed_key"),
                  primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("hour_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bar_json", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_live_candles_hour_start", "live_candles", ["hour_start"])
    op.create_table(
        "live_segments",
        sa.Column("feed_key", sa.String(64), sa.ForeignKey("live_feeds.feed_key"),
                  primary_key=True),
        sa.Column("hour_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True)),
        sa.Column("revision_id", sa.String(200),
                  sa.ForeignKey("dataset_revisions.dataset_revision_id"),
                  unique=True),
    )


def downgrade() -> None:
    op.drop_table("live_segments")
    op.drop_index("ix_live_candles_hour_start", table_name="live_candles")
    op.drop_table("live_candles")
    op.drop_table("live_feeds")
