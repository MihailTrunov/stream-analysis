"""Durable chart range intent above the single-active-import queue."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Connection,
    DateTime,
    Index,
    String,
    Table,
    Text,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError

from .runs import metadata

history_range_requests = Table(
    "history_range_requests",
    metadata,
    Column("request_id", String(36), primary_key=True),
    Column("request_key", String(64), nullable=False, unique=True),
    Column("feed_key", String(64), nullable=False),
    Column("feed_json", Text(), nullable=False),
    Column("range_start", DateTime(timezone=True), nullable=False),
    Column("range_end", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("settled", Boolean(), nullable=False, default=False),
    Column("failure_reason", Text()),
    CheckConstraint("range_end > range_start", name="ck_history_range_bounds"),
)
Index(
    "ix_history_range_pending",
    history_range_requests.c.settled,
    history_range_requests.c.created_at,
)


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True)
class HistoryRange:
    request_id: str
    feed_json: str
    start: datetime
    end: datetime
    failure_reason: str | None


def load_history_range(connection: Connection, request_id: str) -> HistoryRange:
    row = (
        connection.execute(
            select(history_range_requests).where(
                history_range_requests.c.request_id == request_id,
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError("history range request not found")
    return HistoryRange(
        row["request_id"],
        row["feed_json"],
        utc(row["range_start"]),
        utc(row["range_end"]),
        row["failure_reason"],
    )


def create_history_range(
    connection: Connection,
    feed_json: str,
    start: datetime,
    end: datetime,
    *,
    at: datetime,
) -> HistoryRange:
    feed_key = sha256(feed_json.encode()).hexdigest()
    request_key = sha256(f"{feed_key}|{start.isoformat()}|{end.isoformat()}".encode()).hexdigest()
    existing = connection.scalar(
        select(history_range_requests.c.request_id).where(
            history_range_requests.c.request_key == request_key,
        )
    )
    if existing is not None:
        return load_history_range(connection, existing)
    request_id = str(uuid4())
    try:
        with connection.begin_nested():
            connection.execute(
                history_range_requests.insert().values(
                    request_id=request_id,
                    request_key=request_key,
                    feed_key=feed_key,
                    feed_json=feed_json,
                    range_start=start,
                    range_end=end,
                    created_at=at,
                    settled=False,
                )
            )
    except IntegrityError:
        existing = connection.scalar(
            select(history_range_requests.c.request_id).where(
                history_range_requests.c.request_key == request_key,
            )
        )
        if existing is None:
            raise
        request_id = existing
    return load_history_range(connection, request_id)


def settle_history_range(
    connection: Connection,
    request_id: str,
    failure: str | None = None,
) -> None:
    connection.execute(
        update(history_range_requests)
        .where(
            history_range_requests.c.request_id == request_id,
        )
        .values(settled=True, failure_reason=failure)
    )
