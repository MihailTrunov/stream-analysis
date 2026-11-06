"""Actual autonomous detector execution, causal hooks and committed-prefix recovery."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from sqlalchemy import create_engine, func, select
from test_evaluation_runs import (
    clean_build,
    create,
    evaluation_storage,
    fixture_outcome,
    request,
)

from alembic import command
from alembic.config import Config
from market_analysis.application.analysis_sessions import (
    AnalysisRequest,
    AnalysisSessionManager,
    StreamSettings,
)
from market_analysis.application.autonomous_evaluation import EvaluationStopped, execute_evaluation
from market_analysis.application.evaluation_runs import evaluation_events, read_evaluation_run
from market_analysis.code_version import CodeCaptureStatus, CodeVersion
from market_analysis.config import OutcomeSelection
from market_analysis.demo.replay_seed import seed_replay_datasets
from market_analysis.domain import SimulationClockError, Timeframe
from market_analysis.domain.evaluation import EvaluationError, EvaluationStatus
from market_analysis.persistence.dataset_store import DatasetStore
from market_analysis.persistence.detection_contexts import detection_context_snapshots
from market_analysis.persistence.evaluations import (
    evaluation_event_sets,
    evaluation_stream_progress,
)
from market_analysis.persistence.pattern_instances import (
    pattern_instance_transitions,
    pattern_instances,
)


@pytest.fixture
def storage(tmp_path, monkeypatch):
    yield from evaluation_storage(tmp_path, monkeypatch)


def source_events(engine, record):
    with engine.connect() as connection:
        return evaluation_events(connection, str(record.inputs.evaluation_id), eligible_only=False)


@pytest.mark.parametrize("chunk_size", [1, 4, 128])
def test_real_detector_events_context_and_completion_are_chunk_invariant(storage, chunk_size):
    engine, store = storage
    with engine.begin() as connection:
        record = create(connection, store)
    frames = []

    def frame(stream, candle, result, persisted):
        assert all(item.detection_time == candle.bar.timestamp for item in persisted)
        assert len(result.events) == len(persisted)
        frames.append((candle.available_at, result.frame.bar.timestamp))

    final = execute_evaluation(
        engine,
        store,
        str(record.inputs.evaluation_id),
        chunk_size=chunk_size,
        revision_provider=clean_build,
        on_frame=frame,
    )
    assert final.status is EvaluationStatus.COMPLETED
    assert final.cursor_index == record.inputs.execution_bar_count - 1
    events = source_events(engine, final)["events"]
    assert [item["event_kind"] for item in events] == ["CANDIDATE", "ACTIVE", "COMPLETED"]
    assert [item["eligible"] for item in events] == [False, True, True]
    assert len(frames) == final.inputs.streams[0].expected_bars
    for item in events:
        assert item["context"]["values"]["detector"]["state"] == item["event_kind"]
        assert item["outcomes"] == []
    with pytest.raises(EvaluationError, match="fresh execution"):
        execute_evaluation(
            engine, store, str(record.inputs.evaluation_id), revision_provider=clean_build
        )


def test_clock_observer_has_no_future_and_old_prefix_does_not_expand(storage):
    engine, store = storage
    with engine.begin() as connection:
        record = create(connection, store)
    prefixes = []

    def observer(prefix):
        with pytest.raises(SimulationClockError, match="future"):
            prefix.bar_at(prefix.index + 1)
        assert len(prefix.history()) == prefix.index + 1
        prefixes.append(prefix)

    execute_evaluation(
        engine,
        store,
        str(record.inputs.evaluation_id),
        revision_provider=clean_build,
        on_source_step=observer,
    )
    assert len(prefixes[0].history()) == 1
    assert prefixes[-1].index == record.inputs.execution_bar_count - 1


def test_failure_preserves_last_committed_prefix_and_cannot_report_complete(storage):
    engine, store = storage
    with engine.begin() as connection:
        record = create(connection, store)

    def failure(prefix):
        if prefix.index == 12:
            raise RuntimeError("forced mid-run failure")

    with pytest.raises(RuntimeError, match="forced mid-run failure"):
        execute_evaluation(
            engine,
            store,
            str(record.inputs.evaluation_id),
            chunk_size=4,
            revision_provider=clean_build,
            on_source_step=failure,
        )
    with engine.connect() as connection:
        final = read_evaluation_run(connection, str(record.inputs.evaluation_id))
        progress = connection.execute(select(evaluation_stream_progress)).mappings().one()
        assert progress["processed_bars"] == 12
    assert final.status is EvaluationStatus.FAILED and final.cursor_index == 11
    assert final.completed_at is None and "forced mid-run failure" in final.failure_reason
    assert not source_events(engine, final)["complete"]
    assert [item["event_kind"] for item in source_events(engine, final)["events"]] == ["CANDIDATE"]


def test_manual_stop_cancels_without_completing_uncommitted_candles(storage):
    engine, store = storage
    with engine.begin() as connection:
        record = create(connection, store)
    stopped = False

    def stop(prefix):
        nonlocal stopped
        if prefix.index == 12:
            stopped = True

    final = execute_evaluation(
        engine,
        store,
        str(record.inputs.evaluation_id),
        chunk_size=4,
        revision_provider=clean_build,
        on_source_step=stop,
        should_stop=lambda: stopped,
    )
    assert final.status is EvaluationStatus.CANCELLED and final.cursor_index == 11
    assert final.completed_at is None and final.failure_reason is None


def test_all_selected_timeframes_execute_and_outcomes_are_downstream(storage):
    engine, store = storage
    definition = fixture_outcome()
    outcomes = (OutcomeSelection(outcome_id="fixture", outcome_version="1"),)
    selected = request(multihour=True, outcomes=outcomes)
    higher = request(multihour=True, timeframe=Timeframe.M5, outcomes=outcomes).streams[0]
    selected = selected.model_copy(update={"streams": (selected.streams[0], higher)})
    with engine.begin() as connection:
        record = create(connection, store, selected, {definition.identity: definition})
    final = execute_evaluation(
        engine,
        store,
        str(record.inputs.evaluation_id),
        revision_provider=clean_build,
        chunk_size=17,
    )
    assert final.status is EvaluationStatus.DETECTION_COMPLETED
    with engine.connect() as connection:
        rows = connection.execute(select(evaluation_stream_progress)).mappings().all()
        assert {row["processed_bars"] for row in rows} == {300, 60}
        assert len(connection.execute(select(evaluation_event_sets)).all()) > 0
    assert source_events(engine, final)["complete"] is False


def test_invalid_executor_build_marks_run_failed_before_output(storage):
    engine, store = storage
    with engine.begin() as connection:
        record = create(connection, store)
    with pytest.raises(EvaluationError, match="executor build differs"):
        execute_evaluation(
            engine,
            store,
            str(record.inputs.evaluation_id),
            revision_provider=lambda build: CodeVersion(
                build, "b" * 40, False, CodeCaptureStatus.AVAILABLE, "fixture"
            ),
        )
    with engine.connect() as connection:
        final = read_evaluation_run(connection, str(record.inputs.evaluation_id))
    assert final.status is EvaluationStatus.FAILED and final.cursor_index == -1
    assert source_events(engine, final)["events"] == []


def test_interactive_replay_remains_usable_while_evaluation_owns_active_slot(storage):
    engine, store = storage
    with engine.begin() as connection:
        record = create(connection, store)
    ready, proceed = Event(), Event()

    def committed():
        if not ready.is_set():
            ready.set()
            assert proceed.wait(10)

    manager = AnalysisSessionManager()
    selected = request()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            execute_evaluation,
            engine,
            store,
            str(record.inputs.evaluation_id),
            chunk_size=1,
            revision_provider=clean_build,
            on_commit=committed,
        )
        assert ready.wait(10)
        try:
            state = manager.launch(
                AnalysisRequest(
                    dataset_revision_id=selected.dataset_revision_id,
                    instrument_id=selected.instrument_id,
                    selected_start=selected.selected_start,
                    selected_end=selected.selected_end,
                    streams=(StreamSettings(config=selected.streams[0].config),),
                    mode="replay",
                    chart_timeframe=Timeframe.M1,
                )
            )
            stepped = manager.command(state["run_id"], "step")
            assert stepped["visible_bars"] > state["visible_bars"]
        finally:
            proceed.set()
        assert pending.result(timeout=15).status is EvaluationStatus.COMPLETED


@pytest.mark.parametrize("corrupt_source", [False, True])
def test_dedicated_worker_executes_real_offline_run_and_logs_failure_context(
    storage, corrupt_source
):
    engine, store = storage
    with engine.begin() as connection:
        record = create(connection, store)
    if corrupt_source:
        parquet = next(
            (store.root / "datasets" / record.inputs.dataset_revision_id).glob("*.parquet")
        )
        parquet.write_bytes(parquet.read_bytes() + b"corrupted fixture")
    environment = {
        **os.environ,
        "STREAM_ANALYSIS_DATABASE_URL": str(engine.url),
        "STREAM_ANALYSIS_DATA_ROOT": str(store.root),
        "STREAM_ANALYSIS_BUILD_ID": "fixture",
        "STREAM_ANALYSIS_GIT_COMMIT": "a" * 40,
        "STREAM_ANALYSIS_GIT_DIRTY": "false",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "market_analysis.application.worker",
            "evaluation",
            "--run-id",
            str(record.inputs.evaluation_id),
            "--chunk-size",
            "3",
        ],
        cwd=Path(__file__).parents[2],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    with engine.connect() as connection:
        final = read_evaluation_run(connection, str(record.inputs.evaluation_id))
    assert final.status is (
        EvaluationStatus.FAILED if corrupt_source else EvaluationStatus.COMPLETED
    )
    assert result.returncode == (1 if corrupt_source else 0), result.stderr
    if corrupt_source:
        messages = [json.loads(line) for line in result.stderr.splitlines() if line.startswith("{")]
        failure = next(
            item for item in messages if item["message"] == "autonomous detector execution failed"
        )
        assert failure["run_id"] == str(record.inputs.evaluation_id)
        assert failure["dataset_id"] == record.inputs.dataset_revision_id
        assert failure["component"] == "evaluation" and final.failure_reason
    else:
        assert [item["event_kind"] for item in source_events(engine, final)["events"]] == [
            "CANDIDATE",
            "ACTIVE",
            "COMPLETED",
        ]


@pytest.mark.parametrize("fail", [False, True])
def test_postgres_actual_executor_completion_and_partial_failure(tmp_path, monkeypatch, fail):
    url = os.getenv("STREAM_ANALYSIS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("PostgreSQL integration URL is not configured")
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(url)
    store = DatasetStore(tmp_path / "data")
    try:
        with engine.begin() as connection:
            seed_replay_datasets(connection, store)
            record = create(connection, store)

        def observer(prefix):
            if fail and prefix.index == 12:
                raise RuntimeError("PostgreSQL mid-run failure")

        def execute():
            return execute_evaluation(
                engine,
                store,
                str(record.inputs.evaluation_id),
                chunk_size=4,
                revision_provider=clean_build,
                on_source_step=observer,
            )

        if fail:
            with pytest.raises(RuntimeError, match="PostgreSQL mid-run failure"):
                execute()
        else:
            assert execute().status is EvaluationStatus.COMPLETED
        with engine.connect() as connection:
            final = read_evaluation_run(connection, str(record.inputs.evaluation_id))
        assert final.status is (EvaluationStatus.FAILED if fail else EvaluationStatus.COMPLETED)
        events = source_events(engine, final)
        assert len(events["events"]) == (1 if fail else 3)
        assert events["complete"] is not fail
        assert final.cursor_index == (11 if fail else 13)
    finally:
        engine.dispose()


@pytest.mark.parametrize("cancel", [False, True])
def test_first_event_chunk_rolls_back_every_record_on_migrated_sqlite(
    tmp_path, monkeypatch, cancel
):
    url = f"sqlite:///{tmp_path / 'rollback.sqlite'}"
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(url)
    store = DatasetStore(tmp_path / "data")
    try:
        with engine.begin() as connection:
            seed_replay_datasets(connection, store)
            record = create(connection, store)

        def stop_after_event(stream, candle, result, persisted):
            if persisted:
                assert candle.bar.timestamp.minute == 8
                raise (EvaluationStopped if cancel else RuntimeError)("first event in chunk")

        def execute():
            return execute_evaluation(
                engine,
                store,
                str(record.inputs.evaluation_id),
                chunk_size=1,
                revision_provider=clean_build,
                on_frame=stop_after_event,
            )

        if cancel:
            assert execute().status is EvaluationStatus.CANCELLED
        else:
            with pytest.raises(RuntimeError, match="first event in chunk"):
                execute()
        with engine.connect() as connection:
            final = read_evaluation_run(connection, str(record.inputs.evaluation_id))
            assert final.cursor_index == 7
            for table in (
                pattern_instances,
                pattern_instance_transitions,
                detection_context_snapshots,
                evaluation_event_sets,
            ):
                assert connection.scalar(select(func.count()).select_from(table)) == 0
            progress = connection.execute(select(evaluation_stream_progress)).mappings().one()
            assert progress["processed_bars"] == 8
    finally:
        engine.dispose()
