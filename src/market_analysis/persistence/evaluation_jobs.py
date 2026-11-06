"""Durable serial queue control and immutable links to analytical evaluations."""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
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
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from market_analysis.domain.evaluation import EvaluationError
from market_analysis.persistence.runs import metadata


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class EvaluationJobNotFound(EvaluationError):
    pass


evaluation_queue = Table(
    "evaluation_queue",
    metadata,
    Column("singleton", Integer, primary_key=True),
    Column("automatic", Boolean, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("next_position", Integer, nullable=False),
    CheckConstraint("singleton = 1", name="ck_evaluation_queue_singleton"),
    CheckConstraint("revision >= 0 AND next_position >= 0", name="ck_evaluation_queue_progress"),
)
evaluation_jobs = Table(
    "evaluation_jobs",
    metadata,
    Column("job_id", String(36), primary_key=True),
    Column(
        "evaluation_id",
        String(36),
        ForeignKey("evaluation_inputs.evaluation_id"),
        nullable=False,
        unique=True,
    ),
    Column("position", Integer, nullable=False, unique=True),
    Column("status", String(20), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("heartbeat_at", DateTime(timezone=True)),
    Column("lease_token", String(36)),
    Column("active_slot", Integer, unique=True),
    Column("stop_requested", Boolean, nullable=False),
    Column("failure_reason", Text),
    CheckConstraint(
        "status IN ('QUEUED','RUNNING','COMPLETED','CANCELLED','FAILED')",
        name="ck_evaluation_job_status",
    ),
    CheckConstraint("position >= 0", name="ck_evaluation_job_position"),
    CheckConstraint(
        "(status = 'RUNNING' AND active_slot IS NOT NULL AND active_slot = 1 "
        "AND lease_token IS NOT NULL) OR (status <> 'RUNNING' "
        "AND active_slot IS NULL AND lease_token IS NULL)",
        name="ck_evaluation_job_owner",
    ),
    CheckConstraint(
        "(status IN ('COMPLETED','CANCELLED','FAILED') AND finished_at IS NOT NULL) "
        "OR (status IN ('QUEUED','RUNNING') AND finished_at IS NULL)",
        name="ck_evaluation_job_finished",
    ),
)


@dataclass(frozen=True)
class EvaluationJob:
    job_id: str
    evaluation_id: str
    position: int
    status: JobStatus
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    heartbeat_at: datetime | None
    lease_token: str | None
    stop_requested: bool
    failure_reason: str | None


def ensure_queue(connection: Connection) -> None:
    insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
    connection.execute(
        insert(evaluation_queue)
        .values(
            singleton=1,
            automatic=False,
            revision=0,
            next_position=0,
        )
        .on_conflict_do_nothing(index_elements=["singleton"])
    )


def lock_queue(connection: Connection) -> None:
    ensure_queue(connection)
    connection.execute(
        evaluation_queue.update()
        .where(
            evaluation_queue.c.singleton == 1,
        )
        .values(revision=evaluation_queue.c.revision + 1)
    )


def read_job(connection: Connection, job_id: str) -> EvaluationJob:
    row = (
        connection.execute(
            select(evaluation_jobs).where(
                evaluation_jobs.c.job_id == job_id,
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise EvaluationJobNotFound("evaluation job not found")

    def time(name: str) -> datetime | None:
        value = row[name]
        if value is None:
            return None
        assert isinstance(value, datetime)
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    created = time("created_at")
    assert created is not None
    return EvaluationJob(
        row["job_id"],
        row["evaluation_id"],
        row["position"],
        JobStatus(row["status"]),
        created,
        time("started_at"),
        time("finished_at"),
        time("heartbeat_at"),
        row["lease_token"],
        bool(row["stop_requested"]),
        row["failure_reason"],
    )
