"""SQL storage for canonical immutable context payloads; no runtime dependency."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import (
    Column,
    Connection,
    ForeignKey,
    String,
    Table,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.engine import RowMapping

from market_analysis.persistence.runs import metadata

detection_context_snapshots = Table(
    "detection_context_snapshots", metadata,
    Column("snapshot_id", String(36), primary_key=True),
    Column("event_id", String(36), ForeignKey("pattern_instance_transitions.event_id"),
           nullable=False),
    Column("run_id", String(36), ForeignKey("run_snapshots.run_id"), nullable=False),
    Column("context_schema_version", String(100), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    UniqueConstraint("event_id", "context_schema_version", name="uq_event_context_schema"),
)


def load_context_row(
    connection: Connection, event_id: str, context_schema_version: str,
) -> RowMapping | None:
    return connection.execute(select(detection_context_snapshots).where(
        detection_context_snapshots.c.event_id == str(UUID(event_id)),
        detection_context_snapshots.c.context_schema_version == context_schema_version,
    )).mappings().first()


def insert_context_row(connection: Connection, values: Mapping[str, object]) -> None:
    connection.execute(detection_context_snapshots.insert().values(**values))
