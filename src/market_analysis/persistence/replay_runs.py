"""Mutable replay lifecycle rows referencing immutable run snapshots."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Column,
    Connection,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    select,
    update,
)

from market_analysis.persistence.runs import metadata


class ReplayRunError(ValueError):
    """A replay lifecycle or cursor invariant was violated."""


class ReplayStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


_ALLOWED: dict[ReplayStatus, frozenset[ReplayStatus]] = {
    ReplayStatus.CREATED: frozenset({ReplayStatus.RUNNING, ReplayStatus.ABORTED}),
    ReplayStatus.RUNNING: frozenset({
        ReplayStatus.PAUSED, ReplayStatus.COMPLETED, ReplayStatus.FAILED, ReplayStatus.ABORTED,
    }),
    ReplayStatus.PAUSED: frozenset({
        ReplayStatus.RUNNING, ReplayStatus.FAILED, ReplayStatus.ABORTED,
    }),
    ReplayStatus.COMPLETED: frozenset(),
    ReplayStatus.FAILED: frozenset(),
    ReplayStatus.ABORTED: frozenset(),
}

replay_runs = Table(
    "replay_runs",
    metadata,
    Column("run_id", String(36), ForeignKey("run_snapshots.run_id"), primary_key=True),
    Column("selected_start", DateTime(timezone=True), nullable=False),
    Column("selected_end", DateTime(timezone=True), nullable=False),
    Column("status", String(12), nullable=False),
    Column("cursor_index", Integer, nullable=False),
    Column("source_bar_count", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
    Column("failure_reason", Text),
    CheckConstraint("selected_end > selected_start", name="ck_replay_selected_range"),
    CheckConstraint("cursor_index >= -1", name="ck_replay_cursor"),
    CheckConstraint("source_bar_count > 0", name="ck_replay_bar_count"),
    CheckConstraint("cursor_index < source_bar_count", name="ck_replay_cursor_bound"),
    CheckConstraint(
        "status IN ('created','running','paused','completed','failed','aborted')",
        name="ck_replay_status",
    ),
)


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ReplayRunError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _stored_utc(value: datetime) -> datetime:
    # SQLite drops timezone information; all writes use normalized UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ReplayRunRecord:
    run_id: str
    selected_start: datetime
    selected_end: datetime
    status: ReplayStatus
    cursor_index: int
    source_bar_count: int
    created_at: datetime
    completed_at: datetime | None = None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        try:
            UUID(self.run_id)
        except (TypeError, ValueError) as exc:
            raise ReplayRunError("run_id must be a UUID") from exc
        for name in ("selected_start", "selected_end", "created_at"):
            object.__setattr__(self, name, _utc(getattr(self, name), name))
        if self.selected_end <= self.selected_start:
            raise ReplayRunError("selected_end must be after selected_start")
        if not isinstance(self.status, ReplayStatus):
            raise ReplayRunError("status must be a ReplayStatus")
        if isinstance(self.cursor_index, bool) or not isinstance(self.cursor_index, int):
            raise ReplayRunError("cursor_index must be an integer")
        if self.cursor_index < -1:
            raise ReplayRunError("cursor_index must be >= -1")
        if (
            isinstance(self.source_bar_count, bool)
            or not isinstance(self.source_bar_count, int)
            or self.source_bar_count < 1
            or self.cursor_index >= self.source_bar_count
        ):
            raise ReplayRunError("cursor must be within a nonempty source")
        if self.completed_at is not None:
            object.__setattr__(self, "completed_at", _utc(self.completed_at, "completed_at"))
            if self.completed_at < self.created_at:
                raise ReplayRunError("completed_at must follow created_at")
        if self.status in (ReplayStatus.COMPLETED, ReplayStatus.FAILED, ReplayStatus.ABORTED):
            if self.completed_at is None:
                raise ReplayRunError("terminal replay status requires completed_at")
        elif self.completed_at is not None:
            raise ReplayRunError("nonterminal replay status cannot have completed_at")
        if self.status is ReplayStatus.FAILED:
            if not self.failure_reason or not self.failure_reason.strip():
                raise ReplayRunError("failed replay requires failure_reason")
        elif self.failure_reason is not None:
            raise ReplayRunError("failure_reason is valid only for failed replay")


def insert_replay_run(connection: Connection, record: ReplayRunRecord) -> ReplayRunRecord:
    values = asdict(record)
    values["status"] = record.status.value
    connection.execute(replay_runs.insert().values(**values))
    return record


def load_replay_run(connection: Connection, run_id: UUID) -> ReplayRunRecord | None:
    row = connection.execute(
        select(replay_runs).where(replay_runs.c.run_id == str(run_id))
    ).mappings().one_or_none()
    if row is None:
        return None
    return ReplayRunRecord(
        run_id=row["run_id"],
        selected_start=_stored_utc(row["selected_start"]),
        selected_end=_stored_utc(row["selected_end"]),
        status=ReplayStatus(row["status"]),
        cursor_index=row["cursor_index"],
        source_bar_count=row["source_bar_count"],
        created_at=_stored_utc(row["created_at"]),
        completed_at=(
            None if row["completed_at"] is None else _stored_utc(row["completed_at"])
        ),
        failure_reason=row["failure_reason"],
    )


def transition_replay_run(
    connection: Connection,
    run_id: UUID,
    target: ReplayStatus,
    *,
    at: datetime,
    failure_reason: str | None = None,
    exhausted: bool = False,
) -> ReplayRunRecord:
    record = load_replay_run(connection, run_id)
    if record is None:
        raise ReplayRunError("replay run does not exist")
    if not isinstance(target, ReplayStatus) or target not in _ALLOWED[record.status]:
        raise ReplayRunError(f"invalid replay transition: {record.status} -> {target}")
    if target is ReplayStatus.COMPLETED and (
        record.cursor_index < 0 or exhausted is not True
    ):
        raise ReplayRunError("completed replay requires exhausted observable range")
    terminal = target in (ReplayStatus.COMPLETED, ReplayStatus.FAILED, ReplayStatus.ABORTED)
    changed = ReplayRunRecord(
        run_id=record.run_id,
        selected_start=record.selected_start,
        selected_end=record.selected_end,
        status=target,
        cursor_index=record.cursor_index,
        source_bar_count=record.source_bar_count,
        created_at=record.created_at,
        completed_at=at if terminal else None,
        failure_reason=failure_reason,
    )
    result = connection.execute(
        update(replay_runs)
        .where(replay_runs.c.run_id == str(run_id), replay_runs.c.status == record.status.value)
        .values(
            status=changed.status.value,
            completed_at=changed.completed_at,
            failure_reason=changed.failure_reason,
        )
    )
    if result.rowcount != 1:
        raise ReplayRunError("replay status changed concurrently")
    return changed


def advance_replay_cursor(connection: Connection, run_id: UUID, index: int) -> ReplayRunRecord:
    """Record exactly one new observable index; SCRUM-63 owns cursor driving."""
    record = load_replay_run(connection, run_id)
    if record is None:
        raise ReplayRunError("replay run does not exist")
    if (
        record.status is not ReplayStatus.RUNNING
        or isinstance(index, bool)
        or index != record.cursor_index + 1
        or index >= record.source_bar_count
    ):
        raise ReplayRunError("cursor advances once only while running")
    result = connection.execute(
        update(replay_runs)
        .where(
            replay_runs.c.run_id == str(run_id),
            replay_runs.c.status == ReplayStatus.RUNNING.value,
            replay_runs.c.cursor_index == record.cursor_index,
        )
        .values(cursor_index=index)
    )
    if result.rowcount != 1:
        raise ReplayRunError("replay cursor changed concurrently")
    changed = load_replay_run(connection, run_id)
    assert changed is not None
    return changed
