"""Durable historical-import jobs and exactly-once page checkpoints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Connection,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    and_,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError

from market_analysis.domain.market_data import Timeframe
from market_analysis.persistence.runs import metadata


class ImportJobError(ValueError):
    """A checkpoint, lifecycle, or immutable request invariant was violated."""


class ImportStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    COMPLETED = "completed"


import_jobs = Table(
    "import_jobs",
    metadata,
    Column("job_id", String(36), primary_key=True),
    Column("request_key", String(64), nullable=False),
    Column("dataset_id", String(200), nullable=False),
    Column("revision_id", String(36), nullable=False, unique=True),
    Column("instrument_id", String(200), ForeignKey("instruments.instrument_id"), nullable=False),
    Column("timeframe", String(10), nullable=False),
    Column("requested_start", DateTime(timezone=True), nullable=False),
    Column("requested_end", DateTime(timezone=True), nullable=False),
    Column("provider", String(100), nullable=False),
    Column("environment", String(100), nullable=False),
    Column("provider_symbol", String(200), nullable=False),
    Column("account_fingerprint", String(64)),
    Column("calendar_version", String(200), nullable=False),
    Column("status", String(16), nullable=False),
    Column("active_slot", Integer(), unique=True),
    Column("source_id", String(200)),
    Column("next_page_token", Text()),
    Column("fetch_complete", Boolean(), nullable=False, default=False),
    Column("pages_committed", Integer(), nullable=False, default=0),
    Column("bar_count", Integer(), nullable=False, default=0),
    Column("actual_start", DateTime(timezone=True)),
    Column("actual_end", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("heartbeat_at", DateTime(timezone=True)),
    Column("retrieved_at", DateTime(timezone=True)),
    Column("gap_report_json", Text()),
    Column("failure_reason", Text()),
    CheckConstraint("requested_end > requested_start", name="ck_import_requested_range"),
    CheckConstraint("timeframe = '1m'", name="ck_import_mvp_timeframe"),
    CheckConstraint("pages_committed >= 0 AND bar_count >= 0", name="ck_import_progress"),
    CheckConstraint(
        "status IN ('queued','running','interrupted','failed','completed')",
        name="ck_import_status",
    ),
    CheckConstraint(
        "(status IN ('queued','running') AND active_slot IS NOT NULL AND active_slot = 1) OR "
        "(status NOT IN ('queued','running') AND active_slot IS NULL)",
        name="ck_import_active_slot",
    ),
)
Index("ix_import_jobs_request", import_jobs.c.request_key)

import_batches = Table(
    "import_batches",
    metadata,
    Column("job_id", String(36), ForeignKey("import_jobs.job_id"), primary_key=True),
    Column("batch_index", Integer(), primary_key=True),
    Column("relative_path", Text(), nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("bar_count", Integer(), nullable=False),
    Column("first_timestamp", DateTime(timezone=True), nullable=False),
    Column("last_timestamp", DateTime(timezone=True), nullable=False),
    CheckConstraint("batch_index >= 0 AND bar_count > 0", name="ck_import_batch_counts"),
)


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ImportJobError("import timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _stored_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _stamp(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ImportRequest:
    dataset_id: str
    instrument_id: str
    timeframe: Timeframe
    start: datetime
    end: datetime
    provider: str
    environment: str
    provider_symbol: str
    calendar_version: str
    account_fingerprint: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "dataset_id",
            "instrument_id",
            "provider",
            "environment",
            "provider_symbol",
            "calendar_version",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ImportJobError(f"{name} must be non-empty")
        if self.timeframe is not Timeframe.M1:
            raise ImportJobError("MVP acquisition supports M1 only")
        if self.account_fingerprint is not None and (
            len(self.account_fingerprint) != 64
            or any(c not in "0123456789abcdef" for c in self.account_fingerprint)
        ):
            raise ImportJobError("account fingerprint must be a lowercase SHA-256 digest")
        object.__setattr__(self, "start", _utc(self.start))
        object.__setattr__(self, "end", _utc(self.end))
        if self.end <= self.start:
            raise ImportJobError("import end must follow start")

    @property
    def key(self) -> str:
        identity = (
            self.dataset_id,
            self.instrument_id,
            self.timeframe.value,
            _stamp(self.start),
            _stamp(self.end),
            self.provider,
            self.environment,
            self.provider_symbol,
            self.calendar_version,
            self.account_fingerprint,
        )
        return sha256(repr(identity).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ImportJob:
    job_id: str
    request: ImportRequest
    revision_id: str
    status: ImportStatus
    source_id: str | None
    next_page_token: str | None
    fetch_complete: bool
    pages_committed: int
    bar_count: int
    actual_start: datetime | None
    actual_end: datetime | None
    created_at: datetime
    updated_at: datetime
    heartbeat_at: datetime | None
    retrieved_at: datetime | None
    gap_report_json: str | None
    failure_reason: str | None


@dataclass(frozen=True, slots=True)
class ImportBatch:
    job_id: str
    batch_index: int
    relative_path: str
    sha256: str
    bar_count: int
    first_timestamp: datetime
    last_timestamp: datetime


def _job(row: object) -> ImportJob:
    data = dict(row)  # type: ignore[call-overload]
    request = ImportRequest(
        data["dataset_id"],
        data["instrument_id"],
        Timeframe(data["timeframe"]),
        _stored_utc(data["requested_start"]),
        _stored_utc(data["requested_end"]),
        data["provider"],
        data["environment"],
        data["provider_symbol"],
        data["calendar_version"],
        data["account_fingerprint"],
    )
    return ImportJob(
        data["job_id"],
        request,
        data["revision_id"],
        ImportStatus(data["status"]),
        data["source_id"],
        data["next_page_token"],
        data["fetch_complete"],
        data["pages_committed"],
        data["bar_count"],
        None if data["actual_start"] is None else _stored_utc(data["actual_start"]),
        None if data["actual_end"] is None else _stored_utc(data["actual_end"]),
        _stored_utc(data["created_at"]),
        _stored_utc(data["updated_at"]),
        None if data["heartbeat_at"] is None else _stored_utc(data["heartbeat_at"]),
        None if data["retrieved_at"] is None else _stored_utc(data["retrieved_at"]),
        data["gap_report_json"],
        data["failure_reason"],
    )


def load_import_job(connection: Connection, job_id: str) -> ImportJob | None:
    row = (
        connection.execute(select(import_jobs).where(import_jobs.c.job_id == job_id))
        .mappings()
        .one_or_none()
    )
    return None if row is None else _job(row)


def list_import_jobs(connection: Connection, *, limit: int = 100) -> tuple[ImportJob, ...]:
    if not 1 <= limit <= 1000:
        raise ImportJobError("import list limit must be between 1 and 1000")
    rows = connection.execute(
        select(import_jobs)
        .order_by(import_jobs.c.created_at.desc(), import_jobs.c.job_id.desc())
        .limit(limit)
    ).mappings()
    return tuple(_job(row) for row in rows)


def create_import_job(
    connection: Connection,
    request: ImportRequest,
    *,
    job_id: str,
    revision_id: str,
    at: datetime,
    fresh_attempt: bool = False,
) -> ImportJob:
    for value in (job_id, revision_id):
        try:
            UUID(value)
        except (TypeError, ValueError) as exc:
            raise ImportJobError("job and revision IDs must be UUIDs") from exc
    at = _utc(at)
    if not fresh_attempt:
        row = (
            connection.execute(
                select(import_jobs)
                .where(import_jobs.c.request_key == request.key)
                .order_by(
                    (import_jobs.c.status == ImportStatus.COMPLETED.value).desc(),
                    import_jobs.c.created_at.desc(),
                )
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        if row is not None:
            return _job(row)
    try:
        with connection.begin_nested():
            connection.execute(
                import_jobs.insert().values(
                    job_id=job_id,
                    request_key=request.key,
                    dataset_id=request.dataset_id,
                    revision_id=revision_id,
                    instrument_id=request.instrument_id,
                    timeframe=request.timeframe.value,
                    requested_start=request.start,
                    requested_end=request.end,
                    provider=request.provider,
                    environment=request.environment,
                    provider_symbol=request.provider_symbol,
                    account_fingerprint=request.account_fingerprint,
                    calendar_version=request.calendar_version,
                    status=ImportStatus.QUEUED.value,
                    active_slot=1,
                    source_id=None,
                    next_page_token=None,
                    fetch_complete=False,
                    pages_committed=0,
                    bar_count=0,
                    actual_start=None,
                    actual_end=None,
                    created_at=at,
                    updated_at=at,
                    heartbeat_at=None,
                    retrieved_at=None,
                    gap_report_json=None,
                    failure_reason=None,
                )
            )
    except IntegrityError as exc:
        if not fresh_attempt:
            row = (
                connection.execute(
                    select(import_jobs)
                    .where(import_jobs.c.request_key == request.key)
                    .order_by(import_jobs.c.created_at.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
            if row is not None:
                return _job(row)
        raise ImportJobError("another import is already active") from exc
    job = load_import_job(connection, job_id)
    assert job is not None
    return job


def claim_import_job(connection: Connection, *, at: datetime) -> ImportJob | None:
    if (
        connection.scalar(
            select(import_jobs.c.job_id)
            .where(import_jobs.c.status == ImportStatus.RUNNING.value)
            .limit(1)
        )
        is not None
    ):
        return None
    query = (
        select(import_jobs.c.job_id)
        .where(import_jobs.c.status == ImportStatus.QUEUED.value)
        .order_by(import_jobs.c.created_at, import_jobs.c.job_id)
        .limit(1)
    )
    if connection.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    job_id = connection.scalar(query)
    if job_id is None:
        return None
    result = connection.execute(
        update(import_jobs)
        .where(import_jobs.c.job_id == job_id, import_jobs.c.status == ImportStatus.QUEUED.value)
        .values(status=ImportStatus.RUNNING.value, updated_at=_utc(at), heartbeat_at=_utc(at))
    )
    if result.rowcount != 1:
        return None
    return load_import_job(connection, job_id)


def interrupt_running_imports(connection: Connection, *, at: datetime) -> int:
    result = connection.execute(
        update(import_jobs)
        .where(import_jobs.c.status == ImportStatus.RUNNING.value)
        .values(
            status=ImportStatus.INTERRUPTED.value,
            active_slot=None,
            updated_at=_utc(at),
            failure_reason="import worker stopped before completion",
            heartbeat_at=None,
        )
    )
    return result.rowcount


def touch_import_heartbeat(connection: Connection, job_id: str, *, at: datetime) -> bool:
    result = connection.execute(
        update(import_jobs)
        .where(import_jobs.c.job_id == job_id, import_jobs.c.status == ImportStatus.RUNNING.value)
        .values(heartbeat_at=_utc(at))
    )
    return result.rowcount == 1


def resume_import_job(connection: Connection, job_id: str, *, at: datetime) -> ImportJob:
    try:
        with connection.begin_nested():
            result = connection.execute(
                update(import_jobs)
                .where(
                    import_jobs.c.job_id == job_id,
                    import_jobs.c.status == ImportStatus.INTERRUPTED.value,
                )
                .values(
                    status=ImportStatus.QUEUED.value,
                    active_slot=1,
                    updated_at=_utc(at),
                    failure_reason=None,
                    heartbeat_at=None,
                )
            )
    except IntegrityError as exc:
        raise ImportJobError("another import is already active") from exc
    if result.rowcount != 1:
        raise ImportJobError("only an interrupted import can be explicitly resumed")
    job = load_import_job(connection, job_id)
    assert job is not None
    return job


def fail_import_job(connection: Connection, job_id: str, *, at: datetime, reason: str) -> ImportJob:
    if not reason.strip():
        raise ImportJobError("failure reason must be non-empty")
    result = connection.execute(
        update(import_jobs)
        .where(import_jobs.c.job_id == job_id, import_jobs.c.status == ImportStatus.RUNNING.value)
        .values(
            status=ImportStatus.FAILED.value,
            active_slot=None,
            updated_at=_utc(at),
            failure_reason=reason[:1000],
            heartbeat_at=None,
        )
    )
    if result.rowcount != 1:
        raise ImportJobError("only a running import can fail")
    job = load_import_job(connection, job_id)
    assert job is not None
    return job


def list_import_batches(connection: Connection, job_id: str) -> tuple[ImportBatch, ...]:
    rows = connection.execute(
        select(import_batches)
        .where(import_batches.c.job_id == job_id)
        .order_by(import_batches.c.batch_index)
    ).mappings()
    return tuple(
        ImportBatch(
            row["job_id"],
            row["batch_index"],
            row["relative_path"],
            row["sha256"],
            row["bar_count"],
            _stored_utc(row["first_timestamp"]),
            _stored_utc(row["last_timestamp"]),
        )
        for row in rows
    )


def checkpoint_import_page(
    connection: Connection,
    job_id: str,
    *,
    expected_token: str | None,
    next_token: str | None,
    source_id: str,
    batch: ImportBatch | None,
    at: datetime,
) -> ImportJob:
    job = load_import_job(connection, job_id)
    if job is None or job.status is not ImportStatus.RUNNING or job.fetch_complete:
        raise ImportJobError("import is not actively fetching")
    if job.next_page_token != expected_token or not source_id.strip():
        raise ImportJobError("import checkpoint does not match source page")
    if job.source_id is not None and job.source_id != source_id:
        raise ImportJobError("import source changed across pages")
    if next_token is not None and (not next_token or next_token == expected_token):
        raise ImportJobError("import continuation must advance")
    count = job.bar_count
    first, last = job.actual_start, job.actual_end
    if batch is not None:
        if batch.job_id != job_id or batch.batch_index != job.pages_committed:
            raise ImportJobError("import batch index does not advance")
        if last is not None and batch.first_timestamp < last:
            raise ImportJobError("import batches must be strictly ordered")
        connection.execute(
            import_batches.insert().values(
                job_id=batch.job_id,
                batch_index=batch.batch_index,
                relative_path=batch.relative_path,
                sha256=batch.sha256,
                bar_count=batch.bar_count,
                first_timestamp=batch.first_timestamp,
                last_timestamp=batch.last_timestamp,
            )
        )
        count += batch.bar_count
        first = first or batch.first_timestamp
        last = min(batch.last_timestamp + timedelta(minutes=1), job.request.end)
    result = connection.execute(
        update(import_jobs)
        .where(
            and_(
                import_jobs.c.job_id == job_id,
                import_jobs.c.status == ImportStatus.RUNNING.value,
                import_jobs.c.pages_committed == job.pages_committed,
            )
        )
        .values(
            source_id=source_id,
            next_page_token=next_token,
            fetch_complete=next_token is None,
            pages_committed=job.pages_committed + (batch is not None),
            bar_count=count,
            actual_start=first,
            actual_end=last,
            retrieved_at=_utc(at) if next_token is None else None,
            updated_at=_utc(at),
            heartbeat_at=_utc(at),
        )
    )
    if result.rowcount != 1:
        raise ImportJobError("import checkpoint changed concurrently")
    changed = load_import_job(connection, job_id)
    assert changed is not None
    return changed


def complete_import_job(
    connection: Connection, job_id: str, *, at: datetime, gap_report_json: str
) -> ImportJob:
    job = load_import_job(connection, job_id)
    if job is None or job.status is not ImportStatus.RUNNING or not job.fetch_complete:
        raise ImportJobError("import cannot complete before fetch and publication")
    result = connection.execute(
        update(import_jobs)
        .where(import_jobs.c.job_id == job_id, import_jobs.c.status == ImportStatus.RUNNING.value)
        .values(
            status=ImportStatus.COMPLETED.value,
            active_slot=None,
            updated_at=_utc(at),
            gap_report_json=gap_report_json,
            heartbeat_at=None,
        )
    )
    if result.rowcount != 1:
        raise ImportJobError("import status changed concurrently")
    changed = load_import_job(connection, job_id)
    assert changed is not None
    return changed
