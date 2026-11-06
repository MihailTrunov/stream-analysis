"""Serial durable job orchestration around the shared autonomous executor."""

from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, func, select

from market_analysis.application.autonomous_evaluation import execute_evaluation
from market_analysis.application.evaluation_runs import create_evaluation_run, read_evaluation_run
from market_analysis.application.logging import research_logger
from market_analysis.code_version import CodeVersion, capture_code_version
from market_analysis.config import EvaluationPlan
from market_analysis.domain.evaluation import EvaluationError, EvaluationRequest, EvaluationStatus
from market_analysis.persistence.dataset_store import DatasetStore
from market_analysis.persistence.evaluation_jobs import (
    EvaluationJob,
    JobStatus,
    ensure_queue,
    lock_queue,
    read_job,
)
from market_analysis.persistence.evaluation_jobs import (
    evaluation_jobs as jobs,
)
from market_analysis.persistence.evaluation_jobs import (
    evaluation_queue as queue,
)
from market_analysis.persistence.evaluations import (
    claim_evaluation,
    evaluation_runs,
    fail_evaluation,
)
from market_analysis.persistence.transactions import atomic_transaction

LEASE_TIMEOUT = timedelta(seconds=60)


def enqueue_evaluation(connection: Connection, evaluation_id: str, at: datetime) -> EvaluationJob:
    evaluation_id = str(UUID(evaluation_id))
    lock_queue(connection)
    connection.execute(
        select(evaluation_runs.c.evaluation_id)
        .where(
            evaluation_runs.c.evaluation_id == evaluation_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    record = read_evaluation_run(connection, evaluation_id)
    if record is None or record.status is not EvaluationStatus.CREATED:
        raise EvaluationError("only a fresh frozen evaluation can be queued")
    if any(EvaluationPlan.from_canonical_json(s.plan_json).outcomes for s in record.inputs.streams):
        raise EvaluationError(
            "queued outcome execution is unavailable until algorithms are registered"
        )
    if connection.scalar(select(jobs.c.job_id).where(jobs.c.evaluation_id == evaluation_id)):
        raise EvaluationError("evaluation is already queued")
    if (
        connection.execute(
            select(func.count())
            .select_from(jobs)
            .where(
                jobs.c.status == JobStatus.QUEUED.value,
            )
        ).scalar_one()
        >= 1000
    ):
        raise EvaluationError("evaluation queue is full")
    position = connection.execute(select(queue.c.next_position)).scalar_one()
    connection.execute(queue.update().values(next_position=position + 1))
    identity = str(uuid4())
    connection.execute(
        jobs.insert().values(
            job_id=identity,
            evaluation_id=evaluation_id,
            position=position,
            status=JobStatus.QUEUED.value,
            created_at=at,
            stop_requested=False,
        )
    )
    return read_job(connection, identity)


def submit_job(
    connection: Connection,
    request: EvaluationRequest,
    store: DatasetStore,
    *,
    build_id: str,
    at: datetime,
    revision_provider: Callable[[str], CodeVersion] = capture_code_version,
) -> EvaluationJob:
    record = create_evaluation_run(
        connection,
        request,
        store,
        build_id=build_id,
        created_at=at,
        revision_provider=revision_provider,
    )
    return enqueue_evaluation(connection, str(record.inputs.evaluation_id), at)


def set_automatic(connection: Connection, enabled: bool) -> None:
    lock_queue(connection)
    if enabled and connection.scalar(
        select(jobs.c.job_id).where(
            jobs.c.status == JobStatus.RUNNING.value,
            jobs.c.stop_requested.is_(True),
        )
    ):
        raise EvaluationError("wait for active cancellation before restarting the queue")
    connection.execute(queue.update().values(automatic=enabled))


def reorder_waiting(connection: Connection, identities: list[str]) -> None:
    lock_queue(connection)
    waiting = set(
        connection.execute(
            select(jobs.c.job_id).where(
                jobs.c.status == JobStatus.QUEUED.value,
            )
        ).scalars()
    )
    if len(set(identities)) != len(identities) or set(identities) != waiting:
        raise EvaluationError("order must include each currently queued job exactly once")
    position = connection.execute(select(queue.c.next_position)).scalar_one()
    for offset, identity in enumerate(identities):
        connection.execute(
            jobs.update()
            .where(jobs.c.job_id == identity)
            .values(
                position=position + offset,
            )
        )
    connection.execute(queue.update().values(next_position=position + len(identities)))


def cancel_job(connection: Connection, identity: str, at: datetime) -> EvaluationJob:
    lock_queue(connection)
    job = read_job(connection, identity)
    if job.status is JobStatus.RUNNING:
        connection.execute(queue.update().values(automatic=False))
        connection.execute(
            jobs.update().where(jobs.c.job_id == identity).values(stop_requested=True)
        )
    elif job.status is JobStatus.QUEUED:
        changed = connection.execute(
            evaluation_runs.update()
            .where(
                evaluation_runs.c.evaluation_id == job.evaluation_id,
                evaluation_runs.c.status == EvaluationStatus.CREATED.value,
            )
            .values(
                status=EvaluationStatus.CANCELLED.value, revision=evaluation_runs.c.revision + 1
            )
        )
        if changed.rowcount != 1:
            raise EvaluationError("queued evaluation was started outside its queue owner")
        connection.execute(
            jobs.update()
            .where(jobs.c.job_id == identity)
            .values(
                status=JobStatus.CANCELLED.value,
                finished_at=at,
            )
        )
    elif job.status is not JobStatus.CANCELLED:
        raise EvaluationError("only queued or running jobs can be cancelled")
    return read_job(connection, identity)


def claim_next_job(connection: Connection, at: datetime) -> EvaluationJob | None:
    lock_queue(connection)
    if not connection.scalar(select(queue.c.automatic)):
        return None
    if connection.scalar(
        select(evaluation_runs.c.evaluation_id).where(
            evaluation_runs.c.status == EvaluationStatus.RUNNING.value,
        )
    ) or connection.scalar(select(jobs.c.job_id).where(jobs.c.status == JobStatus.RUNNING.value)):
        return None
    identity = connection.scalar(
        select(jobs.c.job_id)
        .where(
            jobs.c.status == JobStatus.QUEUED.value,
        )
        .order_by(jobs.c.position)
        .limit(1)
    )
    if identity is None:
        return None
    job = read_job(connection, identity)
    try:
        record = read_evaluation_run(connection, job.evaluation_id)
        if record is None or record.status is not EvaluationStatus.CREATED:
            raise EvaluationError(
                "queued analytical input is missing or was started outside its job"
            )
    except ValueError as error:
        connection.execute(
            evaluation_runs.update()
            .where(
                evaluation_runs.c.evaluation_id == job.evaluation_id,
                evaluation_runs.c.status == EvaluationStatus.CREATED.value,
            )
            .values(
                status=EvaluationStatus.FAILED.value,
                failure_reason=str(error)[:2000],
                heartbeat_at=at,
                revision=evaluation_runs.c.revision + 1,
            )
        )
        research_logger(component="evaluation-scheduler", run_id=job.evaluation_id).warning(
            "queued evaluation preflight failed",
            extra={"job_id": job.job_id, "failure_reason": str(error)},
        )
        connection.execute(
            jobs.update()
            .where(jobs.c.job_id == identity)
            .values(
                status=JobStatus.FAILED.value,
                finished_at=at,
                failure_reason=str(error)[:2000],
            )
        )
        connection.execute(queue.update().values(automatic=False))
        return None
    try:
        token = claim_evaluation(connection, job.evaluation_id, at)
    except EvaluationError:
        if connection.scalar(
            select(evaluation_runs.c.evaluation_id).where(
                evaluation_runs.c.status == EvaluationStatus.RUNNING.value,
            )
        ):
            return None
        raise
    connection.execute(
        jobs.update()
        .where(jobs.c.job_id == identity)
        .values(
            status=JobStatus.RUNNING.value,
            active_slot=1,
            lease_token=token,
            started_at=at,
            heartbeat_at=at,
        )
    )
    return read_job(connection, identity)


def touch_job(connection: Connection, job: EvaluationJob, at: datetime) -> bool:
    changed = connection.execute(
        jobs.update()
        .where(
            jobs.c.job_id == job.job_id,
            jobs.c.status == JobStatus.RUNNING.value,
            jobs.c.lease_token == job.lease_token,
        )
        .values(heartbeat_at=at)
    )
    if changed.rowcount != 1:
        return False
    connection.execute(
        evaluation_runs.update()
        .where(
            evaluation_runs.c.evaluation_id == job.evaluation_id,
            evaluation_runs.c.status == EvaluationStatus.RUNNING.value,
            evaluation_runs.c.owner_token == job.lease_token,
        )
        .values(heartbeat_at=at)
    )
    return True


def finish_job(connection: Connection, owned: EvaluationJob, at: datetime) -> EvaluationJob:
    lock_queue(connection)
    job = read_job(connection, owned.job_id)
    if job.status is not JobStatus.RUNNING or job.lease_token != owned.lease_token:
        return job
    run = read_evaluation_run(connection, job.evaluation_id)
    assert run is not None
    if run.status is EvaluationStatus.RUNNING:
        raise EvaluationError("active analytical execution cannot be finalized as a job")
    status = {
        EvaluationStatus.COMPLETED: JobStatus.COMPLETED,
        EvaluationStatus.CANCELLED: JobStatus.CANCELLED,
    }.get(run.status, JobStatus.FAILED)
    reason = (
        None
        if status is not JobStatus.FAILED
        else (run.failure_reason or "requested downstream execution has not completed")
    )
    connection.execute(
        jobs.update()
        .where(jobs.c.job_id == job.job_id)
        .values(
            status=status.value,
            active_slot=None,
            lease_token=None,
            finished_at=at,
            failure_reason=reason,
            heartbeat_at=at,
        )
    )
    if status is not JobStatus.COMPLETED:
        connection.execute(queue.update().values(automatic=False))
    return read_job(connection, job.job_id)


def recover_stale(
    connection: Connection, at: datetime, *, timeout: timedelta = LEASE_TIMEOUT
) -> int:
    if timeout.total_seconds() <= 0:
        raise EvaluationError("lease timeout must be positive")
    lock_queue(connection)
    cutoff = at - timeout
    identities = (
        connection.execute(
            select(jobs.c.job_id)
            .where(
                jobs.c.status == JobStatus.RUNNING.value,
                jobs.c.heartbeat_at < cutoff,
            )
            .with_for_update()
        )
        .scalars()
        .all()
    )
    recovered = 0
    for identity in identities:
        job = read_job(connection, identity)
        run = (
            connection.execute(
                select(evaluation_runs).where(
                    evaluation_runs.c.evaluation_id == job.evaluation_id,
                )
            )
            .mappings()
            .one()
        )
        if run["status"] == EvaluationStatus.RUNNING.value:
            if run["owner_token"] != job.lease_token:
                raise EvaluationError("job lease differs from its active analytical owner")
            assert job.lease_token is not None
            fail_evaluation(
                connection,
                job.evaluation_id,
                job.lease_token,
                "evaluation worker lease expired; partial output retained",
                at,
            )
        finish_job(connection, job, at)
        recovered += 1
    orphans = (
        connection.execute(
            select(evaluation_runs).where(
                evaluation_runs.c.status == EvaluationStatus.RUNNING.value,
                evaluation_runs.c.heartbeat_at < cutoff,
                ~evaluation_runs.c.evaluation_id.in_(
                    select(jobs.c.evaluation_id).where(
                        jobs.c.status == JobStatus.RUNNING.value,
                    )
                ),
            )
        )
        .mappings()
        .all()
    )
    for run in orphans:
        fail_evaluation(
            connection,
            run["evaluation_id"],
            run["owner_token"],
            "explicit worker lease expired; partial output retained",
            at,
        )
        connection.execute(
            jobs.update()
            .where(
                jobs.c.evaluation_id == run["evaluation_id"],
                jobs.c.status == JobStatus.QUEUED.value,
            )
            .values(
                status=JobStatus.FAILED.value,
                finished_at=at,
                failure_reason="unowned execution lease expired; partial output retained",
            )
        )
        recovered += 1
    if recovered:
        connection.execute(queue.update().values(automatic=False))
    return recovered


def job_state(connection: Connection, identity: str) -> dict[str, Any]:
    job = read_job(connection, identity)
    result = asdict(job)
    result.pop("lease_token")
    try:
        run = read_evaluation_run(connection, job.evaluation_id)
    except ValueError as error:
        result.update(
            run_status="UNAVAILABLE",
            analytical_error=str(error),
            cursor_index=None,
            source_minutes=None,
            available_at=None,
        )
        return result
    if run is None:
        raise EvaluationError("job analytical input is missing")
    result.update(
        run_status=run.status.value,
        cursor_index=run.cursor_index,
        source_minutes=run.inputs.execution_bar_count,
        available_at=run.available_at,
    )
    return result


def queue_state(connection: Connection, *, history_limit: int = 100) -> dict[str, Any]:
    if not 1 <= history_limit <= 500:
        raise EvaluationError("history_limit must be 1..500")
    ensure_queue(connection)
    control = connection.execute(select(queue)).mappings().one()
    waiting = (
        connection.execute(
            select(jobs.c.job_id)
            .where(
                jobs.c.status == JobStatus.QUEUED.value,
            )
            .order_by(jobs.c.position)
        )
        .scalars()
        .all()
    )
    active = connection.scalar(
        select(jobs.c.job_id).where(jobs.c.status == JobStatus.RUNNING.value)
    )
    recent = (
        connection.execute(
            select(jobs.c.job_id)
            .where(
                jobs.c.status.not_in([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
            )
            .order_by(jobs.c.finished_at.desc(), jobs.c.job_id)
            .limit(history_limit)
        )
        .scalars()
        .all()
    )
    return {
        "automatic": control["automatic"],
        "revision": control["revision"],
        "waiting": [job_state(connection, identity) for identity in waiting],
        "active": None if active is None else job_state(connection, active),
        "recent": [job_state(connection, identity) for identity in recent],
    }


def run_scheduler_once(
    engine: Engine,
    store: DatasetStore,
    *,
    chunk_size: int = 128,
    should_stop: Callable[[], bool] = lambda: False,
    revision_provider: Callable[[str], CodeVersion] = capture_code_version,
    on_commit: Callable[[], None] | None = None,
    on_heartbeat: Callable[[], None] | None = None,
) -> EvaluationJob | None:
    with atomic_transaction(engine) as connection:
        recover_stale(connection, datetime.now(UTC))
        job = None if should_stop() else claim_next_job(connection, datetime.now(UTC))
    if job is None:
        return None
    logger = research_logger(component="evaluation-scheduler", run_id=job.evaluation_id)
    heartbeat_stop = Event()

    def heartbeat() -> None:
        with atomic_transaction(engine) as connection:
            touch_job(connection, job, datetime.now(UTC))
        if on_heartbeat is not None:
            on_heartbeat()

    def periodic() -> None:
        while not heartbeat_stop.wait(5):
            try:
                heartbeat()
            except Exception:
                logger.exception("job heartbeat failed")

    def stopped() -> bool:
        if should_stop():
            return True
        with engine.connect() as connection:
            current = read_job(connection, job.job_id)
        return (
            current.status is not JobStatus.RUNNING
            or current.stop_requested
            or current.lease_token != job.lease_token
        )

    def committed() -> None:
        heartbeat()
        if on_commit is not None:
            on_commit()

    thread = Thread(target=periodic, daemon=True)
    thread.start()
    try:
        execute_evaluation(
            engine,
            store,
            job.evaluation_id,
            owner_token=job.lease_token,
            chunk_size=chunk_size,
            should_stop=stopped,
            revision_provider=revision_provider,
            on_commit=committed,
        )
    except Exception:
        logger.exception("queued evaluation failed", extra={"job_id": job.job_id})
    finally:
        heartbeat_stop.set()
        thread.join(timeout=6)
    with atomic_transaction(engine) as connection:
        return finish_job(connection, job, datetime.now(UTC))
