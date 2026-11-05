"""Transactional completed-minute journal and immutable hourly publication markers."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, String, Table, Text

from .runs import metadata

live_feeds = Table(
    "live_feeds", metadata,
    Column("feed_key", String(64), primary_key=True),
    Column("feed_json", Text, nullable=False),
)
live_candles = Table(
    "live_candles", metadata,
    Column("feed_key", String(64), ForeignKey("live_feeds.feed_key"), primary_key=True),
    Column("timestamp", DateTime(timezone=True), primary_key=True),
    Column("hour_start", DateTime(timezone=True), nullable=False, index=True),
    Column("bar_json", Text, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
)
live_segments = Table(
    "live_segments", metadata,
    Column("feed_key", String(64), ForeignKey("live_feeds.feed_key"), primary_key=True),
    Column("hour_start", DateTime(timezone=True), primary_key=True),
    Column("attempted_at", DateTime(timezone=True)),
    Column("revision_id", String(200), ForeignKey("dataset_revisions.dataset_revision_id"),
           unique=True),
)
