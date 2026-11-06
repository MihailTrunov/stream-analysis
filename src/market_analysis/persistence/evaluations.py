"""Frozen evaluation inputs, serial execution ownership and append-only EventSets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Column,
    Connection,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    case,
    select,
)
from sqlalchemy.exc import IntegrityError

from market_analysis.domain.evaluation import EvaluationError, EvaluationInput, EvaluationStatus
from market_analysis.persistence.runs import metadata

evaluation_inputs = Table(
    "evaluation_inputs",
    metadata,
    Column("evaluation_id", String(36), primary_key=True),
    Column("semantic_key", String(64), nullable=False),
    Column("event_set_id", String(64), nullable=False),
    Column("manifest_json", Text, nullable=False),
    Column("manifest_hash", String(64), nullable=False),
)
evaluation_streams = Table(
    "evaluation_streams",
    metadata,
    Column(
        "evaluation_id", String(36), ForeignKey("evaluation_inputs.evaluation_id"), primary_key=True
    ),
    Column("run_id", String(36), ForeignKey("run_snapshots.run_id"), primary_key=True),
    Column("stream_order", Integer, nullable=False),
    UniqueConstraint("run_id", name="uq_evaluation_source_run"),
    UniqueConstraint("evaluation_id", "stream_order", name="uq_evaluation_stream_order"),
)
evaluation_runs = Table(
    "evaluation_runs",
    metadata,
    Column(
        "evaluation_id", String(36), ForeignKey("evaluation_inputs.evaluation_id"), primary_key=True
    ),
    Column("status", String(30), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("cursor_index", Integer, nullable=False),
    Column("available_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("completed_at", DateTime(timezone=True)),
    Column("heartbeat_at", DateTime(timezone=True)),
    Column("owner_token", String(36)),
    Column("active_slot", Integer, unique=True),
    Column("failure_reason", Text),
    CheckConstraint(
        "status IN ('CREATED','RUNNING','DETECTION_COMPLETED','COMPLETED','FAILED','CANCELLED')",
        name="ck_evaluation_status",
    ),
    CheckConstraint("revision >= 0 AND cursor_index >= -1", name="ck_evaluation_progress"),
    CheckConstraint(
        "(status = 'RUNNING' AND active_slot IS NOT NULL AND active_slot = 1 "
        "AND owner_token IS NOT NULL) OR (status <> 'RUNNING' "
        "AND active_slot IS NULL AND owner_token IS NULL)",
        name="ck_evaluation_active_owner",
    ),
    CheckConstraint(
        "(status = 'COMPLETED' AND completed_at IS NOT NULL) OR "
        "(status <> 'COMPLETED' AND completed_at IS NULL)",
        name="ck_evaluation_completed_time",
    ),
)
evaluation_stream_progress = Table(
    "evaluation_stream_progress",
    metadata,
    Column("evaluation_id", String(36), primary_key=True),
    Column("run_id", String(36), primary_key=True),
    Column("processed_bars", Integer, nullable=False),
    Column("last_available_at", DateTime(timezone=True)),
    ForeignKeyConstraint(
        ["evaluation_id", "run_id"],
        ["evaluation_streams.evaluation_id", "evaluation_streams.run_id"],
    ),
    CheckConstraint("processed_bars >= 0", name="ck_evaluation_stream_progress"),
)
evaluation_event_sets = Table(
    "evaluation_event_sets",
    metadata,
    Column(
        "evaluation_id", String(36), ForeignKey("evaluation_inputs.evaluation_id"), primary_key=True
    ),
    Column(
        "event_id",
        String(36),
        ForeignKey("pattern_instance_transitions.event_id"),
        primary_key=True,
    ),
    Column(
        "context_snapshot_id",
        String(36),
        ForeignKey("detection_context_snapshots.snapshot_id"),
        nullable=False,
    ),
    Column("source_run_id", String(36), ForeignKey("run_snapshots.run_id"), nullable=False),
    Column("event_semantic_ref", String(64), nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("emission_order", Integer, nullable=False),
    Column("within_bar_ordinal", Integer, nullable=False),
    Column("eligible", Integer, nullable=False),
    Column("exclusion_reason", Text),
    UniqueConstraint("evaluation_id", "emission_order", name="uq_evaluation_event_order"),
    CheckConstraint(
        "emission_order >= 0 AND within_bar_ordinal >= 0 AND eligible IN (0,1)",
        name="ck_evaluation_event_member",
    ),
    CheckConstraint(
        "(eligible = 1 AND exclusion_reason IS NULL) OR "
        "(eligible = 0 AND exclusion_reason IS NOT NULL)",
        name="ck_evaluation_exclusion_reason",
    ),
)


@dataclass(frozen=True, slots=True)
class EvaluationRunRecord:
    inputs: EvaluationInput
    status: EvaluationStatus
    revision: int
    cursor_index: int
    available_at: datetime | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    heartbeat_at: datetime | None
    owner_token: str | None
    failure_reason: str | None


def _time(value: datetime | None) -> datetime | None:
    return (
        None
        if value is None
        else (value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC))
    )


def insert_evaluation(connection: Connection, inputs: EvaluationInput, at: datetime) -> None:
    payload = inputs.canonical_json()
    identity = str(inputs.evaluation_id)
    connection.execute(
        evaluation_inputs.insert().values(
            evaluation_id=identity,
            semantic_key=inputs.semantic_key,
            event_set_id=inputs.event_set_id,
            manifest_json=payload,
            manifest_hash=sha256(payload.encode()).hexdigest(),
        )
    )
    connection.execute(
        evaluation_streams.insert(),
        [
            {"evaluation_id": identity, "run_id": str(item.run_id), "stream_order": index}
            for index, item in enumerate(inputs.streams)
        ],
    )
    connection.execute(
        evaluation_stream_progress.insert(),
        [
            {"evaluation_id": identity, "run_id": str(item.run_id), "processed_bars": 0}
            for item in inputs.streams
        ],
    )
    connection.execute(
        evaluation_runs.insert().values(
            evaluation_id=identity,
            status=EvaluationStatus.CREATED.value,
            revision=0,
            cursor_index=-1,
            created_at=at,
        )
    )


def load_evaluation(connection: Connection, evaluation_id: str) -> EvaluationRunRecord | None:
    identity = str(UUID(evaluation_id))
    row = (
        connection.execute(
            select(evaluation_inputs).where(
                evaluation_inputs.c.evaluation_id == identity,
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    try:
        inputs = EvaluationInput.model_validate_json(row["manifest_json"])
        if (
            inputs.canonical_json() != row["manifest_json"]
            or sha256(row["manifest_json"].encode()).hexdigest() != row["manifest_hash"]
            or str(inputs.evaluation_id) != identity
            or inputs.semantic_key != row["semantic_key"]
            or inputs.event_set_id != row["event_set_id"]
        ):
            raise EvaluationError("evaluation manifest identity/hash mismatch")
    except (ValueError, TypeError, KeyError) as exc:
        raise EvaluationError("invalid immutable evaluation manifest") from exc
    linked = (
        connection.execute(
            select(evaluation_streams.c.run_id)
            .where(
                evaluation_streams.c.evaluation_id == identity,
            )
            .order_by(evaluation_streams.c.stream_order)
        )
        .scalars()
        .all()
    )
    if linked != [str(item.run_id) for item in inputs.streams]:
        raise EvaluationError("evaluation stream links differ from manifest")
    lifecycle = (
        connection.execute(
            select(evaluation_runs).where(
                evaluation_runs.c.evaluation_id == identity,
            )
        )
        .mappings()
        .one()
    )
    created_at = _time(lifecycle["created_at"])
    assert created_at is not None
    return EvaluationRunRecord(
        inputs,
        EvaluationStatus(lifecycle["status"]),
        lifecycle["revision"],
        lifecycle["cursor_index"],
        _time(lifecycle["available_at"]),
        created_at,
        _time(lifecycle["started_at"]),
        _time(lifecycle["completed_at"]),
        _time(lifecycle["heartbeat_at"]),
        lifecycle["owner_token"],
        lifecycle["failure_reason"],
    )


def claim_evaluation(connection: Connection, evaluation_id: str, at: datetime) -> str:
    token = str(uuid4())
    try:
        with connection.begin_nested():
            changed = connection.execute(
                evaluation_runs.update()
                .where(
                    evaluation_runs.c.evaluation_id == str(UUID(evaluation_id)),
                    evaluation_runs.c.status == EvaluationStatus.CREATED.value,
                )
                .values(
                    status=EvaluationStatus.RUNNING.value,
                    active_slot=1,
                    owner_token=token,
                    started_at=at,
                    heartbeat_at=at,
                    revision=evaluation_runs.c.revision + 1,
                )
            )
            if changed.rowcount != 1:
                raise EvaluationError("evaluation is not available for a fresh execution")
    except IntegrityError as exc:
        raise EvaluationError("another evaluation already owns the active execution slot") from exc
    return token


def advance_evaluation(
    connection: Connection,
    evaluation_id: str,
    token: str,
    *,
    cursor_index: int,
    available_at: datetime,
    at: datetime,
) -> None:
    changed = connection.execute(
        evaluation_runs.update()
        .where(
            evaluation_runs.c.evaluation_id == str(UUID(evaluation_id)),
            evaluation_runs.c.status == EvaluationStatus.RUNNING.value,
            evaluation_runs.c.owner_token == token,
            evaluation_runs.c.cursor_index <= cursor_index,
            (
                evaluation_runs.c.available_at.is_(None)
                | (evaluation_runs.c.available_at <= available_at)
            ),
        )
        .values(
            cursor_index=cursor_index,
            available_at=available_at,
            heartbeat_at=case(
                (
                    evaluation_runs.c.heartbeat_at.is_(None)
                    | (evaluation_runs.c.heartbeat_at < at),
                    at,
                ),
                else_=evaluation_runs.c.heartbeat_at,
            ),
            revision=evaluation_runs.c.revision + 1,
        )
    )
    if changed.rowcount != 1:
        raise EvaluationError("stale evaluation owner or backwards progress")


def finish_detection(connection: Connection, evaluation_id: str, token: str, at: datetime) -> None:
    record = load_evaluation(connection, evaluation_id)
    if (
        record is None
        or record.cursor_index != record.inputs.execution_bar_count - 1
        or (record.available_at != record.inputs.selected_end)
    ):
        raise EvaluationError("detector execution has not reached the pinned end")
    progress = (
        connection.execute(
            select(evaluation_stream_progress).where(
                evaluation_stream_progress.c.evaluation_id == str(record.inputs.evaluation_id),
            )
        )
        .mappings()
        .all()
    )
    completed = {item["run_id"]: item for item in progress}
    for stream in record.inputs.streams:
        row = completed.get(str(stream.run_id))
        if (
            row is None
            or row["processed_bars"] != stream.expected_bars
            or (_time(row["last_available_at"]) != stream.expected_last_available_at)
        ):
            raise EvaluationError("an analytical stream has not completed its pinned candles")
    changed = connection.execute(
        evaluation_runs.update()
        .where(
            evaluation_runs.c.evaluation_id == str(UUID(evaluation_id)),
            evaluation_runs.c.status == EvaluationStatus.RUNNING.value,
            evaluation_runs.c.owner_token == token,
        )
        .values(
            status=EvaluationStatus.DETECTION_COMPLETED.value,
            active_slot=None,
            owner_token=None,
            heartbeat_at=at,
            revision=evaluation_runs.c.revision + 1,
        )
    )
    if changed.rowcount != 1:
        raise EvaluationError("stale evaluation owner cannot finish detection")


def fail_evaluation(
    connection: Connection,
    evaluation_id: str,
    token: str,
    reason: str,
    at: datetime,
) -> None:
    changed = connection.execute(
        evaluation_runs.update()
        .where(
            evaluation_runs.c.evaluation_id == str(UUID(evaluation_id)),
            evaluation_runs.c.status == EvaluationStatus.RUNNING.value,
            evaluation_runs.c.owner_token == token,
        )
        .values(
            status=EvaluationStatus.FAILED.value,
            active_slot=None,
            owner_token=None,
            heartbeat_at=at,
            failure_reason=reason[:2000],
            revision=evaluation_runs.c.revision + 1,
        )
    )
    if changed.rowcount != 1:
        raise EvaluationError("stale evaluation owner cannot mark failure")


def advance_evaluation_stream(
    connection: Connection,
    evaluation_id: str,
    run_id: str,
    token: str,
    available_at: datetime,
) -> None:
    record = load_evaluation(connection, evaluation_id)
    if (
        record is None
        or record.status is not EvaluationStatus.RUNNING
        or record.owner_token != token
    ):
        raise EvaluationError("only the active owner can advance an evaluation stream")
    stream = next((item for item in record.inputs.streams if str(item.run_id) == run_id), None)
    if stream is None or available_at > stream.expected_last_available_at:
        raise EvaluationError("evaluation stream close is outside the pinned source coverage")
    changed = connection.execute(
        evaluation_stream_progress.update()
        .where(
            evaluation_stream_progress.c.evaluation_id == evaluation_id,
            evaluation_stream_progress.c.run_id == run_id,
            evaluation_stream_progress.c.processed_bars < stream.expected_bars,
            evaluation_stream_progress.c.last_available_at.is_(None)
            | (evaluation_stream_progress.c.last_available_at < available_at),
        )
        .values(
            processed_bars=evaluation_stream_progress.c.processed_bars + 1,
            last_available_at=available_at,
        )
    )
    if changed.rowcount != 1:
        raise EvaluationError("evaluation stream repeated or exceeded a completed candle")


def mark_evaluation_completed(connection: Connection, evaluation_id: str, at: datetime) -> None:
    """Application must first verify all requested stages and outcome observations."""
    changed = connection.execute(
        evaluation_runs.update()
        .where(
            evaluation_runs.c.evaluation_id == str(UUID(evaluation_id)),
            evaluation_runs.c.status == EvaluationStatus.DETECTION_COMPLETED.value,
        )
        .values(
            status=EvaluationStatus.COMPLETED.value,
            completed_at=at,
            revision=evaluation_runs.c.revision + 1,
        )
    )
    if changed.rowcount != 1:
        raise EvaluationError("only fully detected evaluations can complete")


def cancel_evaluation(connection: Connection, evaluation_id: str, token: str, at: datetime) -> None:
    changed = connection.execute(
        evaluation_runs.update()
        .where(
            evaluation_runs.c.evaluation_id == str(UUID(evaluation_id)),
            evaluation_runs.c.status == EvaluationStatus.RUNNING.value,
            evaluation_runs.c.owner_token == token,
        )
        .values(
            status=EvaluationStatus.CANCELLED.value,
            active_slot=None,
            owner_token=None,
            heartbeat_at=at,
            revision=evaluation_runs.c.revision + 1,
        )
    )
    if changed.rowcount != 1:
        raise EvaluationError("stale evaluation owner cannot cancel execution")


def touch_evaluation(connection: Connection, evaluation_id: str, token: str, at: datetime) -> bool:
    """Refresh only a live owner; a blocked heartbeat cannot move time backwards."""
    changed = connection.execute(
        evaluation_runs.update()
        .where(
            evaluation_runs.c.evaluation_id == str(UUID(evaluation_id)),
            evaluation_runs.c.status == EvaluationStatus.RUNNING.value,
            evaluation_runs.c.owner_token == token,
        )
        .values(
            heartbeat_at=case(
                (
                    evaluation_runs.c.heartbeat_at.is_(None)
                    | (evaluation_runs.c.heartbeat_at < at),
                    at,
                ),
                else_=evaluation_runs.c.heartbeat_at,
            )
        )
    )
    return changed.rowcount == 1
