"""Durable serial queue, token fencing, actual worker execution and recovery."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DatabaseError
from test_evaluation_runs import clean_build, create, evaluation_storage, request

from alembic import command
from alembic.config import Config
from market_analysis.application.analysis_sessions import (
    AnalysisRequest,
    AnalysisSessionManager,
    StreamSettings,
)
from market_analysis.application.autonomous_evaluation import execute_evaluation
from market_analysis.application.evaluation_runs import evaluation_events, read_evaluation_run
from market_analysis.application.evaluation_scheduler import (
    cancel_job,
    claim_next_job,
    enqueue_evaluation,
    finish_job,
    job_state,
    queue_state,
    recover_stale,
    reorder_waiting,
    run_scheduler_once,
    set_automatic,
    touch_job,
)
from market_analysis.demo.replay_seed import seed_replay_datasets
from market_analysis.domain.evaluation import EvaluationError, EvaluationStatus
from market_analysis.persistence.dataset_store import DatasetStore
from market_analysis.persistence.evaluation_jobs import JobStatus, evaluation_jobs, read_job
from market_analysis.persistence.evaluations import (
    evaluation_inputs,
    evaluation_runs,
    fail_evaluation,
)
from market_analysis.persistence.transactions import atomic_transaction


def now():
    return datetime.now(UTC)


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("STREAM_ANALYSIS_BUILD_ID", "fixture")
    monkeypatch.setenv("STREAM_ANALYSIS_GIT_COMMIT", "a" * 40)
    monkeypatch.setenv("STREAM_ANALYSIS_GIT_DIRTY", "false")
    monkeypatch.setenv("STREAM_ANALYSIS_SEED_REPLAY_DEMO", "0")
    yield from evaluation_storage(tmp_path, monkeypatch)


def add(engine, store):
    with atomic_transaction(engine) as connection:
        record = create(connection, store)
        return enqueue_evaluation(connection, str(record.inputs.evaluation_id), now())


def resume(engine):
    with atomic_transaction(engine) as connection:
        set_automatic(connection, True)


def test_jobs_persist_and_execute_serially_with_real_events(storage):
    engine, store = storage
    first, second = add(engine, store), add(engine, store)
    with atomic_transaction(engine) as connection:
        assert not queue_state(connection)["automatic"]
        before = read_evaluation_run(connection, first.evaluation_id).inputs.canonical_json()
    assert run_scheduler_once(engine, store, revision_provider=clean_build) is None
    resume(engine)
    assert run_scheduler_once(engine, store, revision_provider=clean_build).job_id == first.job_id
    with engine.connect() as connection:
        assert read_job(connection, first.job_id).status is JobStatus.COMPLETED
        assert read_job(connection, second.job_id).status is JobStatus.QUEUED
        assert (
            read_evaluation_run(connection, first.evaluation_id).inputs.canonical_json() == before
        )
        assert (
            len(evaluation_events(connection, first.evaluation_id, eligible_only=False)["events"])
            == 3
        )
        assert "lease_token" not in job_state(connection, first.job_id)
    assert (
        run_scheduler_once(engine, store, revision_provider=clean_build).status
        is JobStatus.COMPLETED
    )


def test_reorder_remove_and_restart_preserve_frozen_inputs(storage):
    engine, store = storage
    first, second, third = (add(engine, store) for _ in range(3))
    with atomic_transaction(engine) as connection:
        reorder_waiting(connection, [second.job_id, first.job_id, third.job_id])
        cancel_job(connection, third.job_id, now())
        assert (
            read_evaluation_run(connection, third.evaluation_id).status
            is EvaluationStatus.CANCELLED
        )
        with pytest.raises(EvaluationError, match="exactly once"):
            reorder_waiting(connection, [first.job_id])
    restarted = create_engine(engine.url)
    try:
        with atomic_transaction(restarted) as connection:
            state = queue_state(connection)
            assert not state["automatic"] and state["active"] is None
            assert [job["job_id"] for job in state["waiting"]] == [second.job_id, first.job_id]
            assert state["recent"][0]["status"] is JobStatus.CANCELLED
    finally:
        restarted.dispose()


def test_cancel_running_preserves_partial_output_and_requires_explicit_restart(storage):
    engine, store = storage
    first, second = add(engine, store), add(engine, store)
    resume(engine)
    commits = 0

    def committed():
        nonlocal commits
        commits += 1
        if commits == 9:
            with atomic_transaction(engine) as connection:
                assert cancel_job(connection, first.job_id, now()).stop_requested
                with pytest.raises(EvaluationError, match="wait for active cancellation"):
                    set_automatic(connection, True)

    final = run_scheduler_once(
        engine,
        store,
        chunk_size=1,
        revision_provider=clean_build,
        on_commit=committed,
    )
    assert final.status is JobStatus.CANCELLED
    with atomic_transaction(engine) as connection:
        run = read_evaluation_run(connection, first.evaluation_id)
        assert run.status is EvaluationStatus.CANCELLED and run.cursor_index == 8
        assert (
            len(evaluation_events(connection, first.evaluation_id, eligible_only=False)["events"])
            == 1
        )
        assert not queue_state(connection)["automatic"]
        assert read_job(connection, second.job_id).status is JobStatus.QUEUED
    assert run_scheduler_once(engine, store, revision_provider=clean_build) is None
    resume(engine)
    assert run_scheduler_once(engine, store, revision_provider=clean_build).job_id == second.job_id


def test_stale_lease_fences_old_worker_and_pauses_remaining_queue(storage):
    engine, store = storage
    first, second = add(engine, store), add(engine, store)
    resume(engine)
    at = now()
    with atomic_transaction(engine) as connection:
        owned = claim_next_job(connection, at)
        assert owned.job_id == first.job_id
        assert claim_next_job(connection, at) is None
    with atomic_transaction(engine) as connection:
        assert recover_stale(connection, at + timedelta(seconds=61)) == 1
        assert not touch_job(connection, owned, at + timedelta(seconds=62))
        assert read_job(connection, first.job_id).status is JobStatus.FAILED
        assert (
            read_evaluation_run(connection, first.evaluation_id).status is EvaluationStatus.FAILED
        )
        assert read_job(connection, second.job_id).status is JobStatus.QUEUED
        assert not queue_state(connection)["automatic"]
    with pytest.raises(EvaluationError, match="not owned"):
        execute_evaluation(
            engine,
            store,
            first.evaluation_id,
            owner_token=owned.lease_token,
            revision_provider=clean_build,
        )


def test_fresh_heartbeat_prevents_stale_recovery(storage):
    engine, store = storage
    first = add(engine, store)
    resume(engine)
    at = now()
    with atomic_transaction(engine) as connection:
        owned = claim_next_job(connection, at)
        assert touch_job(connection, owned, at + timedelta(seconds=30))
        assert recover_stale(connection, at + timedelta(seconds=61)) == 0
        assert recover_stale(connection, at + timedelta(seconds=91)) == 1
        assert "lease expired" in read_job(connection, first.job_id).failure_reason


def test_completed_run_with_lost_job_receipt_reconciles_without_reexecution(storage):
    engine, store = storage
    first = add(engine, store)
    resume(engine)
    at = now()
    with atomic_transaction(engine) as connection:
        owned = claim_next_job(connection, at)
    execute_evaluation(
        engine,
        store,
        first.evaluation_id,
        owner_token=owned.lease_token,
        revision_provider=clean_build,
    )
    with atomic_transaction(engine) as connection:
        assert recover_stale(connection, at + timedelta(seconds=61)) == 1
        assert read_job(connection, first.job_id).status is JobStatus.COMPLETED
        assert not queue_state(connection)["automatic"]


@pytest.mark.parametrize("corrupt", [False, True])
def test_actual_default_worker_consumes_queue_and_persists_failure_summary(storage, corrupt):
    engine, store = storage
    job = add(engine, store)
    resume(engine)
    if corrupt:
        parquet = next((store.root / "datasets" / request().dataset_revision_id).glob("*.parquet"))
        parquet.write_bytes(parquet.read_bytes() + b"corrupt fixture")
    result = subprocess.run(
        [sys.executable, "-m", "market_analysis.application.worker", "evaluation", "--once"],
        cwd=Path(__file__).parents[2],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    with atomic_transaction(engine) as connection:
        final = read_job(connection, job.job_id)
        assert final.status is (JobStatus.FAILED if corrupt else JobStatus.COMPLETED)
        if corrupt:
            assert final.failure_reason and not queue_state(connection)["automatic"]
            logs = [json.loads(line) for line in result.stderr.splitlines() if line.startswith("{")]
            failure = next(item for item in logs if item["message"] == "queued evaluation failed")
            assert failure["job_id"] == job.job_id and failure["run_id"] == job.evaluation_id


def test_interactive_chart_remains_available_during_actual_queued_execution(storage):
    engine, store = storage
    job = add(engine, store)
    resume(engine)
    ready, proceed, heartbeat_seen = Event(), Event(), Event()

    def committed():
        if not ready.is_set():
            ready.set()
            assert proceed.wait(15)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            run_scheduler_once,
            engine,
            store,
            chunk_size=1,
            revision_provider=clean_build,
            on_commit=committed,
            on_heartbeat=heartbeat_seen.set,
        )
        assert ready.wait(15)
        try:
            heartbeat_seen.clear()
            assert heartbeat_seen.wait(8), (
                "lease heartbeat must run independently of candle commits"
            )
            selected = request()
            manager = AnalysisSessionManager()
            state = manager.launch(
                AnalysisRequest(
                    dataset_revision_id=selected.dataset_revision_id,
                    instrument_id=selected.instrument_id,
                    selected_start=selected.selected_start,
                    selected_end=selected.selected_end,
                    streams=(StreamSettings(config=selected.streams[0].config),),
                )
            )
            assert manager.command(state["run_id"], "step")["visible_bars"] > state["visible_bars"]
            with engine.connect() as connection:
                assert read_job(connection, job.job_id).status is JobStatus.RUNNING
        finally:
            proceed.set()
        assert future.result(timeout=20).status is JobStatus.COMPLETED


def test_queue_api_and_atomic_preflight(storage, monkeypatch):
    from market_analysis.api.app import app

    engine, _ = storage
    with TestClient(app) as client:
        monkeypatch.setenv("STREAM_ANALYSIS_GIT_DIRTY", "true")
        assert (
            client.post("/evaluation-jobs", json=request().model_dump(mode="json")).status_code
            == 422
        )
        with engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(evaluation_inputs)) == 0
            assert connection.scalar(select(func.count()).select_from(evaluation_jobs)) == 0
        monkeypatch.setenv("STREAM_ANALYSIS_GIT_DIRTY", "false")
        responses = [
            client.post("/evaluation-jobs", json=request().model_dump(mode="json"))
            for _ in range(2)
        ]
        assert all(response.status_code == 201 for response in responses), [
            r.text for r in responses
        ]
        ids = [response.json()["job_id"] for response in responses]
        assert "lease_token" not in responses[0].json()
        assert not client.get("/evaluation-queue").json()["automatic"]
        reordered = client.put("/evaluation-queue/order", json={"job_ids": list(reversed(ids))})
        assert reordered.status_code == 200
        assert reordered.json()["waiting"][0]["job_id"] == ids[1]
        assert client.post("/evaluation-queue/start").json()["automatic"]
        assert not client.post("/evaluation-queue/pause").json()["automatic"]
        assert client.post(f"/evaluation-jobs/{ids[0]}/cancel").json()["status"] == "CANCELLED"
        assert client.get(f"/evaluation-jobs/{uuid4()}").status_code == 404
        assert client.get("/evaluation-queue?history_limit=501").status_code == 422


def test_postgres_concurrent_claims_and_actual_execution(
    isolated_postgres_url, tmp_path, monkeypatch
):
    url = isolated_postgres_url
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(url)
    store = DatasetStore(tmp_path / "data")
    try:
        with atomic_transaction(engine) as connection:
            seed_replay_datasets(connection, store)
        first, second = add(engine, store), add(engine, store)
        resume(engine)
        barrier = Barrier(2)

        def claim():
            barrier.wait(timeout=10)
            with atomic_transaction(engine) as connection:
                return claim_next_job(connection, now())

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: claim(), range(2)))
        winners = [result for result in results if result is not None]
        assert len(winners) == 1 and winners[0].job_id == first.job_id
        owned = winners[0]
        assert (
            execute_evaluation(
                engine,
                store,
                owned.evaluation_id,
                owner_token=owned.lease_token,
                revision_provider=clean_build,
            ).status
            is EvaluationStatus.COMPLETED
        )
        with atomic_transaction(engine) as connection:
            assert finish_job(connection, owned, now()).status is JobStatus.COMPLETED
        assert (
            run_scheduler_once(engine, store, revision_provider=clean_build).job_id == second.job_id
        )
        with engine.begin() as connection, pytest.raises(DatabaseError, match="immutable"):
            connection.execute(
                text("UPDATE evaluation_jobs SET evaluation_id=:id WHERE job_id=:job"),
                {"id": second.evaluation_id, "job": first.job_id},
            )
        with pytest.raises(RuntimeError, match="job history must be retained"):
            command.downgrade(Config("alembic.ini"), "20261106_18")
    finally:
        engine.dispose()


def test_worker_crash_preserves_prefix_and_restart_waits_for_explicit_start(storage):
    engine, store = storage
    first, second = add(engine, store), add(engine, store)
    resume(engine)
    script = """
import os
from pathlib import Path
from sqlalchemy import create_engine
from market_analysis.application.evaluation_scheduler import run_scheduler_once
from market_analysis.persistence.dataset_store import DatasetStore
count = 0
def crash():
    global count
    count += 1
    if count == 9:
        os._exit(23)
run_scheduler_once(create_engine(os.environ['STREAM_ANALYSIS_DATABASE_URL']),
    DatasetStore(Path(os.environ['STREAM_ANALYSIS_DATA_ROOT'])), chunk_size=1, on_commit=crash)
"""
    crashed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
    )
    assert crashed.returncode == 23, crashed.stderr
    expired = now() - timedelta(seconds=120)
    with atomic_transaction(engine) as connection:
        assert read_job(connection, first.job_id).status is JobStatus.RUNNING
        assert read_evaluation_run(connection, first.evaluation_id).cursor_index == 8
        connection.execute(
            evaluation_jobs.update()
            .where(
                evaluation_jobs.c.job_id == first.job_id,
            )
            .values(heartbeat_at=expired)
        )
        connection.execute(
            evaluation_runs.update()
            .where(
                evaluation_runs.c.evaluation_id == first.evaluation_id,
            )
            .values(heartbeat_at=expired)
        )
    restarted = subprocess.run(
        [sys.executable, "-m", "market_analysis.application.worker", "evaluation", "--once"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert restarted.returncode == 0, restarted.stderr
    with atomic_transaction(engine) as connection:
        assert read_job(connection, first.job_id).status is JobStatus.FAILED
        assert read_evaluation_run(connection, first.evaluation_id).cursor_index == 8
        records = evaluation_events(connection, first.evaluation_id, eligible_only=False)
        assert len(records["events"]) == 1 and not records["complete"]
        assert not queue_state(connection)["automatic"]
        assert read_job(connection, second.job_id).status is JobStatus.QUEUED


def test_migrated_sqlite_queue_preserves_job_link_and_history(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'jobs.sqlite'}"
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(url)
    store = DatasetStore(tmp_path / "data")
    try:
        with atomic_transaction(engine) as connection:
            seed_replay_datasets(connection, store)
        first, second = add(engine, store), add(engine, store)
        with engine.begin() as connection, pytest.raises(DatabaseError, match="immutable"):
            connection.execute(
                evaluation_jobs.update()
                .where(
                    evaluation_jobs.c.job_id == first.job_id,
                )
                .values(evaluation_id=second.evaluation_id)
            )
        with engine.begin() as connection, pytest.raises(DatabaseError, match="retained"):
            connection.execute(evaluation_jobs.delete())
        with pytest.raises(RuntimeError, match="job history must be retained"):
            command.downgrade(Config("alembic.ini"), "20261106_18")
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261106_19"
            )
            assert read_job(connection, first.job_id).evaluation_id == first.evaluation_id
    finally:
        engine.dispose()


@pytest.mark.parametrize("format_id", [str, str.upper, lambda value: value.replace("-", "")])
def test_explicit_worker_cannot_bypass_a_queued_job(storage, format_id):
    engine, store = storage
    job = add(engine, store)
    with pytest.raises(EvaluationError, match="scheduler ownership"):
        execute_evaluation(
            engine, store, format_id(job.evaluation_id), revision_provider=clean_build
        )
    with engine.connect() as connection:
        assert read_evaluation_run(connection, job.evaluation_id).status is EvaluationStatus.CREATED
        assert read_job(connection, job.job_id).status is JobStatus.QUEUED


def test_corrupt_frozen_inputs_fail_claim_but_keep_queue_status_readable(storage):
    engine, store = storage
    job = add(engine, store)
    resume(engine)
    # Metadata-created fixture deliberately has no immutable-data triggers.
    with atomic_transaction(engine) as connection:
        connection.execute(
            evaluation_inputs.update()
            .where(
                evaluation_inputs.c.evaluation_id == job.evaluation_id,
            )
            .values(manifest_hash="0" * 64)
        )
        assert claim_next_job(connection, now()) is None
        state = queue_state(connection)
        assert not state["automatic"]
        assert state["recent"][0]["status"] is JobStatus.FAILED
        assert state["recent"][0]["run_status"] == "UNAVAILABLE"
        assert state["recent"][0]["analytical_error"]


def test_stale_explicit_run_also_pauses_the_queue(storage):
    from market_analysis.persistence.evaluations import claim_evaluation

    engine, store = storage
    at = now()
    with atomic_transaction(engine) as connection:
        record = create(connection, store)
        claim_evaluation(connection, str(record.inputs.evaluation_id), at)
        set_automatic(connection, True)
        assert recover_stale(connection, at + timedelta(seconds=61)) == 1
        assert not queue_state(connection)["automatic"]
        assert read_evaluation_run(connection, str(record.inputs.evaluation_id)).status is (
            EvaluationStatus.FAILED
        )


@pytest.mark.parametrize("format_id", [str.upper, lambda value: value.replace("-", "")])
def test_nonqueued_explicit_execution_accepts_equivalent_uuid_formats(storage, format_id):
    engine, store = storage
    with atomic_transaction(engine) as connection:
        record = create(connection, store)
    final = execute_evaluation(
        engine,
        store,
        format_id(str(record.inputs.evaluation_id)),
        revision_provider=clean_build,
    )
    assert final.status is EvaluationStatus.COMPLETED


def test_postgres_recovery_rechecks_heartbeat_after_waiting_for_row_lock(
    isolated_postgres_url,
    tmp_path,
    monkeypatch,
):
    from sqlalchemy import event

    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", isolated_postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(isolated_postgres_url)
    store = DatasetStore(tmp_path / "data")
    at = now()
    try:
        with atomic_transaction(engine) as connection:
            seed_replay_datasets(connection, store)
        job = add(engine, store)
        resume(engine)
        with atomic_transaction(engine) as connection:
            owned = claim_next_job(connection, at)
        ready, release, query_started = Event(), Event(), Event()

        def query(connection, cursor, statement, parameters, context, many):
            if "evaluation_jobs" in statement and "FOR UPDATE" in statement:
                query_started.set()

        event.listen(engine, "before_cursor_execute", query)

        def heartbeat():
            with atomic_transaction(engine) as connection:
                assert touch_job(connection, owned, at + timedelta(seconds=30))
                ready.set()
                assert release.wait(10)

        def recover():
            with atomic_transaction(engine) as connection:
                return recover_stale(connection, at + timedelta(seconds=61))

        with ThreadPoolExecutor(max_workers=2) as pool:
            beat = pool.submit(heartbeat)
            assert ready.wait(10)
            recovered = pool.submit(recover)
            try:
                assert query_started.wait(10)
            finally:
                release.set()
            beat.result(timeout=10)
            assert recovered.result(timeout=10) == 0
        with atomic_transaction(engine) as connection:
            assert read_job(connection, job.job_id).status is JobStatus.RUNNING
            assert queue_state(connection)["automatic"]
            fail_evaluation(
                connection, job.evaluation_id, owned.lease_token, "fixture cleanup", now()
            )
            finish_job(connection, owned, now())
    finally:
        engine.dispose()
