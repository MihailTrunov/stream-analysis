from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from market_analysis.application.replay_run import (
    complete_replay,
    create_replay_run,
    load_replay_context,
    step_replay,
)
from market_analysis.config import (
    ComponentSelection,
    ConfigParameter,
    DetectionAnalysisConfig,
    detection_config_hash,
)
from market_analysis.config.component_registry import BUILTIN_COMPONENT_PARAMETERS
from market_analysis.domain import (
    BAR_CHECKSUM_VERSION,
    Bar,
    BarSequence,
    DatasetLineage,
    Instrument,
    SimulationClock,
    Timeframe,
    ValidationStatus,
    canonical_bar_checksum,
)
from market_analysis.persistence.market_data import (
    DatasetMembership,
    DatasetRevision,
    register_dataset_lineage,
    register_dataset_revision,
    register_instrument,
)
from market_analysis.persistence.replay_runs import (
    ReplayRunError,
    ReplayStatus,
    advance_replay_cursor,
    load_replay_run,
    transition_replay_run,
)
from market_analysis.persistence.runs import load_run_snapshot, metadata

START = datetime(2026, 9, 27, 12, tzinfo=UTC)
SPECS = BUILTIN_COMPONENT_PARAMETERS
SEED_BARS = (
    Bar("US30", Timeframe.M1, START, Decimal("100"), Decimal("101"),
        Decimal("99"), Decimal("100")),
    Bar("US30", Timeframe.M1, START + timedelta(minutes=1), Decimal("100"),
        Decimal("102"), Decimal("100"), Decimal("101")),
)


def prepare(connection) -> None:
    register_instrument(connection, Instrument("US30", "US 30", "cal-v1", 1, Decimal("1")))
    register_dataset_revision(connection, DatasetRevision(
        dataset_revision_id="rev-1", dataset_id="study", source_id="seed",
        provider="seed", retrieved_at=START, created_at=START,
        normalization_version="1", calendar_version="cal-v1",
        manifest_format_version="1", manifest_ref="datasets/rev-1/manifest.json",
        memberships=(DatasetMembership(
            "US30", Timeframe.M1, START, START + timedelta(minutes=2), 2,
        ),),
    ))
    register_dataset_lineage(connection, DatasetLineage(
        dataset_revision_id="rev-1", source_dataset_id="seed-1",
        instrument_id="US30", timeframe=Timeframe.M1,
        requested_start=START, requested_end=START + timedelta(minutes=2),
        actual_start=START, actual_end=START + timedelta(minutes=2),
        bar_count=2, acquired_at=START, validation_status=ValidationStatus.PASS,
        provider_request_json='{"dataset":"seed-1"}',
        source_checksum="a" * 64, canonical_checksum=canonical_bar_checksum(SEED_BARS),
        checksum_version=BAR_CHECKSUM_VERSION, dataset_format_version="parquet-v1",
    ))


def config() -> DetectionAnalysisConfig:
    return DetectionAnalysisConfig(
        instrument_id="US30", calendar_id="cal-v1",
        components=(ComponentSelection(component_id="ema", component_version="1"),),
    )


def test_typed_canonical_config_round_trip_preserves_decimal_and_timestamp() -> None:
    typed = DetectionAnalysisConfig(
        instrument_id="US30", calendar_id="cal-v1",
        components=(ComponentSelection(
            component_id="fixture", component_version="1",
            parameters=(
                ConfigParameter(name="threshold", value=Decimal("1.20")),
                ConfigParameter(name="anchor", value=START),
            ),
        ),),
    )
    restored = DetectionAnalysisConfig.from_canonical_json(typed.canonical_json())
    assert restored.canonical_json() == typed.canonical_json()
    values = {item.name: item.value for item in restored.components[0].parameters}
    assert isinstance(values["anchor"], datetime)
    assert isinstance(values["threshold"], Decimal)


def test_replay_run_pins_resolved_snapshot_and_serializes_deterministically() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    with engine.begin() as connection:
        prepare(connection)
        created = create_replay_run(
            connection, run_id=run_id, dataset_revision_id="rev-1",
            detection_config=config(), selected_start=START,
            selected_end=START + timedelta(minutes=2), created_at=START,
            build_id="git-build-1", component_parameters=SPECS,
        )
        loaded = load_replay_context(connection, run_id, component_parameters=SPECS)
        assert loaded == created
        assert loaded is not None
        assert loaded.canonical_json() == created.canonical_json()
        assert '"cursor_index":-1' in created.canonical_json()
        assert not hasattr(created, "bars")
        assert created.snapshot.detection_config_hash == detection_config_hash(
            created.detection_config, component_parameters=SPECS
        )
        assert '"name":"period","value":45' in created.snapshot.detection_config_json
        assert created.snapshot.calendar_version == "cal-v1"
        assert created.snapshot.build_id == "git-build-1"
        assert load_run_snapshot(connection, run_id) == created.snapshot
    engine.dispose()


def test_replay_run_lifecycle_and_cursor_are_explicit() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    with engine.begin() as connection:
        prepare(connection)
        create_replay_run(
            connection, run_id=run_id, dataset_revision_id="rev-1",
            detection_config=config(), selected_start=START,
            selected_end=START + timedelta(minutes=2), created_at=START,
            build_id="build-1", component_parameters=SPECS,
        )
        with pytest.raises(ReplayRunError, match="only while running"):
            advance_replay_cursor(connection, run_id, 0)
        transition_replay_run(connection, run_id, ReplayStatus.RUNNING, at=START)
        with pytest.raises(ReplayRunError, match="exhausted observable range"):
            transition_replay_run(connection, run_id, ReplayStatus.COMPLETED, at=START)
        assert advance_replay_cursor(connection, run_id, 0).cursor_index == 0
        with pytest.raises(ReplayRunError, match="advances once"):
            advance_replay_cursor(connection, run_id, 0)
        with pytest.raises(ReplayRunError, match="advances once"):
            advance_replay_cursor(connection, run_id, True)
        transition_replay_run(
            connection, run_id, ReplayStatus.PAUSED, at=START + timedelta(seconds=1)
        )
        with pytest.raises(ReplayRunError, match="only while running"):
            advance_replay_cursor(connection, run_id, 1)
        transition_replay_run(
            connection, run_id, ReplayStatus.RUNNING, at=START + timedelta(seconds=2)
        )
        assert advance_replay_cursor(connection, run_id, 1).cursor_index == 1
        with pytest.raises(ReplayRunError, match="only while running"):
            advance_replay_cursor(connection, run_id, 2)
        with pytest.raises(ReplayRunError, match="exhausted observable range"):
            transition_replay_run(
                connection, run_id, ReplayStatus.COMPLETED, at=START + timedelta(seconds=3)
            )
        finished = transition_replay_run(
            connection, run_id, ReplayStatus.COMPLETED,
            at=START + timedelta(seconds=3), exhausted=True,
        )
        assert finished.completed_at == START + timedelta(seconds=3)
        assert load_replay_run(connection, run_id) == finished
        with pytest.raises(ReplayRunError, match="invalid replay transition"):
            transition_replay_run(connection, run_id, ReplayStatus.RUNNING, at=START)
    engine.dispose()


def test_replay_failure_requires_reason_and_does_not_mutate_snapshot() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    with engine.begin() as connection:
        prepare(connection)
        created = create_replay_run(
            connection, run_id=run_id, dataset_revision_id="rev-1",
            detection_config=config(), selected_start=START,
            selected_end=START + timedelta(minutes=2), created_at=START,
            build_id="build-1", component_parameters=SPECS,
        )
        transition_replay_run(connection, run_id, ReplayStatus.RUNNING, at=START)
        with pytest.raises(ReplayRunError, match="failure_reason"):
            transition_replay_run(connection, run_id, ReplayStatus.FAILED, at=START)
        current = load_replay_run(connection, run_id)
        assert current is not None and current.status is ReplayStatus.RUNNING
        transition_replay_run(
            connection, run_id, ReplayStatus.FAILED,
            at=START + timedelta(seconds=1), failure_reason="fixture failure",
        )
        assert load_run_snapshot(connection, run_id) == created.snapshot
    engine.dispose()


def test_replay_rejects_missing_dataset_and_tampered_hash() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    with engine.begin() as connection:
        with pytest.raises(ReplayRunError, match="dataset revision does not exist"):
            create_replay_run(
                connection, run_id=run_id, dataset_revision_id="rev-1",
                detection_config=config(), selected_start=START,
                selected_end=START + timedelta(minutes=2), created_at=START,
                build_id="build-1", component_parameters=SPECS,
            )
        prepare(connection)
        with pytest.raises(ReplayRunError, match="calendar identity"):
            create_replay_run(
                connection, run_id=run_id, dataset_revision_id="rev-1",
                detection_config=DetectionAnalysisConfig(
                    instrument_id="US30", calendar_id="wrong"
                ), selected_start=START, selected_end=START + timedelta(minutes=2),
                created_at=START, build_id="build-1",
            )
        create_replay_run(
            connection, run_id=run_id, dataset_revision_id="rev-1",
            detection_config=config(), selected_start=START,
            selected_end=START + timedelta(minutes=2), created_at=START,
            build_id="build-1", component_parameters=SPECS,
        )
        connection.execute(
            text("UPDATE run_snapshots SET detection_config_hash = :bad WHERE run_id = :id"),
            {"bad": "0" * 64, "id": str(run_id)},
        )
        with pytest.raises(ReplayRunError, match="hash differs"):
            load_replay_context(connection, run_id, component_parameters=SPECS)
    engine.dispose()


def test_replay_clock_step_and_completion_match_pinned_snapshot() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    with engine.begin() as connection:
        prepare(connection)
        context = create_replay_run(
            connection, run_id=run_id, dataset_revision_id="rev-1",
            detection_config=config(), selected_start=START + timedelta(minutes=1),
            selected_end=START + timedelta(minutes=2), created_at=START,
            build_id="build-1", component_parameters=SPECS,
        )
        clock = SimulationClock(
            BarSequence(context.lineage, SEED_BARS),
            selected_start=context.lifecycle.selected_start,
            selected_end=context.lifecycle.selected_end,
        )
        wrong = SimulationClock(BarSequence(context.lineage, SEED_BARS))
        transition_replay_run(connection, run_id, ReplayStatus.RUNNING, at=START)
        with pytest.raises(ReplayRunError, match="does not match"):
            step_replay(connection, run_id, wrong, component_parameters=SPECS)
        with pytest.raises(ReplayRunError, match="not exhausted"):
            complete_replay(
                connection, run_id, clock, at=START, component_parameters=SPECS
            )
        step_replay(connection, run_id, clock, component_parameters=SPECS)
        assert not clock.current.is_visible
        step_replay(connection, run_id, clock, component_parameters=SPECS)
        assert clock.current.is_visible
        with pytest.raises(ReplayRunError, match="no next"):
            step_replay(connection, run_id, clock, component_parameters=SPECS)
        finished = complete_replay(
            connection, run_id, clock, at=START + timedelta(minutes=2),
            component_parameters=SPECS,
        )
        assert finished.status is ReplayStatus.COMPLETED
        assert finished.cursor_index == 1
    engine.dispose()


def test_replay_snapshot_pins_multiple_instances_of_one_ema_definition() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    configured = DetectionAnalysisConfig(
        instrument_id="US30", calendar_id="cal-v1",
        components=(
            ComponentSelection(component_id="ema", component_version="1",
                               instance_id="trend_ema"),
            ComponentSelection(component_id="ema", component_version="1",
                               instance_id="fast_ema",
                               parameters=(ConfigParameter(name="period", value=9),)),
        ),
    )
    with engine.begin() as connection:
        prepare(connection)
        created = create_replay_run(
            connection, run_id=run_id, dataset_revision_id="rev-1",
            detection_config=configured, selected_start=START,
            selected_end=START + timedelta(minutes=2), created_at=START,
            build_id="build-1",
        )
        loaded = load_replay_context(connection, run_id)
        assert loaded == created
        assert loaded is not None
        assert [item.effective_instance_id for item in loaded.detection_config.components] == [
            "fast_ema", "trend_ema"
        ]
        assert [item.parameters[0].value for item in loaded.detection_config.components] == [
            9, 45
        ]
    engine.dispose()
