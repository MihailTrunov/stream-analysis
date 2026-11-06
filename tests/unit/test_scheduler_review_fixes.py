"""Regressions for the three independent Astra scheduler findings."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import pytest
import test_evaluation_scheduler as queue_tests
from sqlalchemy import create_engine, event
from test_evaluation_runs import clean_build, create

from alembic import command
from alembic.config import Config
from market_analysis.application.autonomous_evaluation import execute_evaluation
from market_analysis.application.evaluation_runs import read_evaluation_run
from market_analysis.application.evaluation_scheduler import (
    queue_state,
    recover_stale,
    run_scheduler_once,
    set_automatic,
)
from market_analysis.demo.replay_seed import seed_replay_datasets
from market_analysis.domain.evaluation import EvaluationError, EvaluationStatus
from market_analysis.persistence.dataset_store import DatasetStore
from market_analysis.persistence.evaluation_jobs import JobStatus, read_job
from market_analysis.persistence.evaluations import (
    advance_evaluation,
    claim_evaluation,
    fail_evaluation,
    touch_evaluation,
)
from market_analysis.persistence.transactions import atomic_transaction

storage = queue_tests.storage


def test_explicit_heartbeat_is_monotonic_and_cannot_revive_an_old_owner(storage):
    engine, store = storage
    at = queue_tests.now()
    with atomic_transaction(engine) as connection:
        record = create(connection, store)
        identity = str(record.inputs.evaluation_id)
        token = claim_evaluation(connection, identity, at)
        assert touch_evaluation(connection, identity, token, at + timedelta(seconds=2))
        assert touch_evaluation(connection, identity, token, at + timedelta(seconds=1))
        advance_evaluation(
            connection,
            identity,
            token,
            cursor_index=0,
            available_at=record.inputs.initialization_origin + timedelta(minutes=1),
            at=at + timedelta(seconds=1),
        )
        assert read_evaluation_run(connection, identity).heartbeat_at == at + timedelta(seconds=2)
        assert not touch_evaluation(connection, identity, "wrong-owner", at + timedelta(seconds=3))
        fail_evaluation(connection, identity, token, "fixture cleanup", at + timedelta(seconds=3))
        assert not touch_evaluation(connection, identity, token, at + timedelta(seconds=4))


@pytest.mark.parametrize("size", [0, -1, 2001, True, 1.5])
def test_invalid_chunk_size_does_not_consume_a_job(storage, size):
    engine, store = storage
    job = queue_tests.add(engine, store)
    queue_tests.resume(engine)
    with atomic_transaction(engine) as connection:
        before = queue_state(connection)
    with pytest.raises(EvaluationError, match="chunk_size must be"):
        run_scheduler_once(engine, store, chunk_size=size, revision_provider=clean_build)
    with atomic_transaction(engine) as connection:
        assert read_job(connection, job.job_id).status is JobStatus.QUEUED
        assert read_evaluation_run(connection, job.evaluation_id).status is EvaluationStatus.CREATED
        assert queue_state(connection) == before


def test_explicit_heartbeat_covers_loading_and_exits_with_its_owner(storage, monkeypatch):
    engine, real_store = storage
    with atomic_transaction(engine) as connection:
        record = create(connection, real_store)
        identity = str(record.inputs.evaluation_id)
        set_automatic(connection, True)
    monkeypatch.setattr(
        "market_analysis.application.autonomous_evaluation.HEARTBEAT_INTERVAL",
        0.02,
        raising=False,
    )
    loading, release = Event(), Event()

    class SlowSource:
        def load_sequence(self, *args, **kwargs):
            loading.set()
            assert release.wait(10)
            return real_store.load_sequence(*args, **kwargs)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            execute_evaluation, engine, SlowSource(), identity, revision_provider=clean_build
        )
        assert loading.wait(10)
        try:
            # Short real lease periods exercise the same policy without a one-minute test.
            Event().wait(0.35)
            with atomic_transaction(engine) as connection:
                assert (
                    recover_stale(connection, queue_tests.now(), timeout=timedelta(seconds=0.2))
                    == 0
                )
                assert queue_state(connection)["automatic"]
        finally:
            release.set()
        assert pending.result(timeout=10).status is EvaluationStatus.COMPLETED


def test_postgres_orphan_recovery_rechecks_a_concurrent_progress_heartbeat(
    isolated_postgres_url,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", isolated_postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(isolated_postgres_url)
    store = DatasetStore(tmp_path / "data")
    at = queue_tests.now()
    held, release, recovery_waiting = Event(), Event(), Event()
    try:
        with atomic_transaction(engine) as connection:
            seed_replay_datasets(connection, store)
            record = create(connection, store)
            identity = str(record.inputs.evaluation_id)
            token = claim_evaluation(connection, identity, at)

        def query(connection, cursor, statement, parameters, context, many):
            locked_read = (
                "evaluation_runs.heartbeat_at <" in statement and "FOR UPDATE" in statement
            )
            stale_failure = statement.startswith("UPDATE evaluation_runs") and (
                isinstance(parameters, dict) and parameters.get("status") == "FAILED"
            )
            if locked_read or stale_failure:
                recovery_waiting.set()

        event.listen(engine, "before_cursor_execute", query)

        def progress():
            with atomic_transaction(engine) as connection:
                advance_evaluation(
                    connection,
                    identity,
                    token,
                    cursor_index=0,
                    available_at=record.inputs.initialization_origin + timedelta(minutes=1),
                    at=at + timedelta(seconds=30),
                )
                held.set()
                assert release.wait(10)

        def recover():
            with atomic_transaction(engine) as connection:
                return recover_stale(connection, at + timedelta(seconds=61))

        with ThreadPoolExecutor(max_workers=2) as pool:
            healthy = pool.submit(progress)
            assert held.wait(10)
            recovery = pool.submit(recover)
            try:
                assert recovery_waiting.wait(10)
            finally:
                release.set()
            healthy.result(timeout=10)
            assert recovery.result(timeout=10) == 0
        with engine.connect() as connection:
            assert read_evaluation_run(connection, identity).status is EvaluationStatus.RUNNING
    finally:
        engine.dispose()
