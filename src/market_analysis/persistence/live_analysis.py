"""Live analytical inputs: immutable bootstrap/feed manifest and append-only close receipts."""

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Table,
    Text,
)

from .runs import metadata

live_analysis_inputs = Table(
    "live_analysis_inputs", metadata,
    Column("input_id", String(200), primary_key=True),
    Column("session_id", String(36), ForeignKey("analysis_session_revisions.session_id"),
           nullable=False, unique=True),
    Column("manifest_json", Text, nullable=False),
    Column("manifest_hash", String(64), nullable=False),
)
live_analysis_closes = Table(
    "live_analysis_closes", metadata,
    Column("input_id", String(200), ForeignKey("live_analysis_inputs.input_id"), primary_key=True),
    Column("timestamp", DateTime(timezone=True), primary_key=True),
    Column("feed_key", String(64), nullable=False),
    Column("sha256", String(64), nullable=False),
    ForeignKeyConstraint(["feed_key", "timestamp"],
                         ["live_candles.feed_key", "live_candles.timestamp"]),
)
live_analysis_advances = Table(
    "live_analysis_advances", metadata,
    Column("input_id", String(200), ForeignKey("live_analysis_inputs.input_id"), primary_key=True),
    Column("available_at", DateTime(timezone=True), primary_key=True),
)
