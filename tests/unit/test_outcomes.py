"""Typed, causal, immutable outcomes over declared same-day M1 windows."""

from dataclasses import replace
from datetime import time, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DatabaseError
from test_detection_contexts import drive
from test_detector_runtime import bar, chain_series

from alembic import command
from alembic.config import Config
from market_analysis.application.context_capture import load_detection_context
from market_analysis.application.outcome_evaluation import OutcomeResult, evaluate_outcome
from market_analysis.application.replay_catalog import (
    PATTERN_DEFINITIONS,
    demo_detection_config,
    detector_bindings,
)
from market_analysis.application.replay_event_store import ReplayEventStore
from market_analysis.code_version import CodeCaptureStatus, CodeVersion
from market_analysis.config import (
    ConfigParameter,
    EvaluationPlan,
    OutcomeSelection,
    detection_config_hash,
)
from market_analysis.detection import CONTINUATION_V1, DetectorRuntime
from market_analysis.domain import (
    BarSequence,
    DatasetLineage,
    SessionWindow,
    Timeframe,
    TradingCalendar,
    ValidationStatus,
    canonical_bar_checksum,
)
from market_analysis.domain.aggregation import CandleAggregator
from market_analysis.domain.market_data import Instrument
from market_analysis.domain.outcomes import (
    OutcomeDefinition,
    OutcomeError,
    OutcomeEventReference,
    OutcomeObservation,
    OutputType,
    ReferenceMode,
    ResultStatus,
    outcome_semantic_key,
)
from market_analysis.patterns import ParameterSpec, ParameterType
from market_analysis.persistence.market_data import (
    DatasetMembership,
    DatasetRevision,
    register_dataset_lineage,
    register_dataset_revision,
    register_instrument,
)
from market_analysis.persistence.outcomes import load_outcome_observation
from market_analysis.persistence.runs import create_run_snapshot, load_run_snapshot, metadata

HORIZON = ParameterSpec("horizon", ParameterType.INTEGER, 2, minimum=Decimal(1))
CALENDAR = TradingCalendar(
    "cal-v1",
    "demo-v1",
    "fixture",
    "fixture",
    "US30",
    "UTC",
    time(0),
    (SessionWindow("open", time(0), time(23, 59)),),
)


def definition(kind=OutputType.NUMERIC, **kwargs):
    return OutcomeDefinition(
        outcome_id="fixture",
        outcome_version="1",
        output_type=kind,
        parameters=(HORIZON,),
        units="points" if kind is OutputType.NUMERIC else "category",
        categories=("UP", "DOWN") if kind is OutputType.CATEGORICAL else (),
        **kwargs,
    )


@pytest.fixture
def engine():
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    yield engine
    engine.dispose()


def setup_source(
    connection,
    selected,
    *,
    future=3,
    missing=(),
    timeframe=Timeframe.M1,
    selection_values=(),
):
    bars = (
        *chain_series(),
        bar(15, "150", "160", "119"),
        *(
            bar(index, str(160 + 10 * (index - 16)), str(165 + 10 * (index - 16)), "140")
            for index in range(16, 16 + future)
            if index not in missing
        ),
    )
    pattern = CONTINUATION_V1
    if timeframe is Timeframe.M1:
        _, store, _, snapshots = drive(
            connection,
            outcome=selected.outcome_id,
            outcome_specs=selected.parameters,
            outcome_values=selection_values,
        )
        snapshot = snapshots[-1]
        source = next(
            item
            for record in store._instances.values()
            for item in record.detector_events
            if item.event_id == snapshot.event_id
        )
    else:
        bars = tuple(bar(index, str(10 + index % 2), "12", "9") for index in range(100))
        config = demo_detection_config("US30").model_copy(
            update={
                "calendar_id": CALENDAR.calendar_id,
                "timeframe": timeframe,
            }
        )
        run_id = uuid4()
        plan = EvaluationPlan(
            detection_config_hash=detection_config_hash(
                config, pattern_definitions=PATTERN_DEFINITIONS
            ),
            context_schema_version="detection-time-context-v1",
            outcomes=(
                OutcomeSelection(
                    outcome_id=selected.outcome_id, outcome_version="1", parameters=selection_values
                ),
            ),
        )
        create_run_snapshot(
            connection,
            run_id=run_id,
            dataset_revision_id="context-fixture",
            calendar_version=CALENDAR.version,
            build_id="fixture",
            detection_config=config,
            evaluation_plan=plan,
            pattern_definitions=PATTERN_DEFINITIONS,
            outcome_parameters={selected.identity: selected.parameters},
        )
        runtime = DetectorRuntime(
            config,
            detector_bindings(config),
            run_id=str(run_id),
            dataset_revision_id="context-fixture",
            calendar=CALENDAR,
            pinned_calendar_version=CALENDAR.version,
        )
        store = ReplayEventStore(PATTERN_DEFINITIONS)
        aggregator = CandleAggregator(timeframe, CALENDAR)
        for minute in bars:
            for candle in aggregator.push(minute):
                result = runtime.process_bar(candle.bar)
                store(
                    connection,
                    candle.bar,
                    result,
                    runtime.binding_fingerprint,
                    available_at=candle.available_at,
                )
        source = next(
            item for record in store._instances.values() for item in record.detector_events
        )
        snapshot = load_detection_context(connection, source.event_id)
        pattern = PATTERN_DEFINITIONS[(source.pattern_id, source.pattern_version)]
    lineage = DatasetLineage(
        dataset_revision_id="context-fixture",
        source_dataset_id="fixture",
        instrument_id="US30",
        timeframe=Timeframe.M1,
        requested_start=bars[0].timestamp,
        requested_end=bars[-1].timestamp + timedelta(minutes=1),
        actual_start=bars[0].timestamp,
        actual_end=bars[-1].timestamp + timedelta(minutes=1),
        bar_count=len(bars),
        acquired_at=bars[0].timestamp,
        validation_status=ValidationStatus.PASS,
        provider_request_json='{"fixture":true}',
        source_checksum="a" * 64,
        canonical_checksum=canonical_bar_checksum(bars),
        checksum_version="bar-sequence-v1",
        dataset_format_version="parquet-v1",
    )
    register_instrument(
        connection,
        Instrument(
            "US30",
            "US30",
            "cal-v1",
            2,
            Decimal("0.01"),
            (),
        ),
    )
    register_dataset_revision(
        connection,
        DatasetRevision(
            "context-fixture",
            "fixture",
            "fixture",
            "fixture",
            bars[0].timestamp,
            bars[0].timestamp,
            "fixture",
            "demo-v1",
            "fixture",
            (
                DatasetMembership(
                    "US30", Timeframe.M1, lineage.requested_start, lineage.requested_end, len(bars)
                ),
            ),
            "fixture-manifest",
        ),
    )
    register_dataset_lineage(connection, lineage)
    sequence = BarSequence(lineage, bars)
    return dict(
        event=source,
        pattern=pattern,
        definition=selected,
        definitions={selected.identity: selected},
        bar_sequence=sequence,
        calendar=CALENDAR,
    ), snapshot


@pytest.mark.parametrize("kind", list(OutputType))
def test_all_output_types_roundtrip_and_bounded_inputs(engine, kind):
    selected = definition(kind)
    with engine.begin() as connection:
        arguments, snapshot = setup_source(connection, selected)
        reference = OutcomeEventReference(
            semantic_ref="b" * 64,
            kind="market",
            dataset_revision_id="context-fixture",
            instrument_id="US30",
            timeframe=Timeframe.M1,
            detection_config_hash=arguments["event"].detection_config_hash,
            event_time=bar(16).timestamp,
            detection_time=bar(16).timestamp,
            available_at=bar(17).timestamp,
            within_bar_ordinal=0,
        )

        def algorithm(source):
            assert [item.timestamp for item in source.bars] == [
                bar(16).timestamp,
                bar(17).timestamp,
            ]
            assert source.window.reference_price == Decimal(150)
            assert len(source.events) == 1
            with pytest.raises(TypeError):
                source.context["detector"]["state"] = "CHANGED"
            values = {
                OutputType.NUMERIC: source.bars[-1].close - source.window.reference_price,
                OutputType.BOOLEAN: True,
                OutputType.CATEGORICAL: "UP",
                OutputType.EVENT_REFERENCE: source.events[0],
            }
            return OutcomeResult(ResultStatus.AVAILABLE, values[kind])

        result = evaluate_outcome(connection, **arguments, algorithm=algorithm, events=(reference,))
        assert result.status is ResultStatus.AVAILABLE
        assert result.lineage.source_event_semantic_ref == snapshot.source_event_semantic_ref
        assert result.lineage.canonical_checksum == (
            arguments["bar_sequence"].lineage.canonical_checksum
        )
        assert load_outcome_observation(connection, result.observation_id) == result
        assert OutcomeObservation.model_validate_json(result.canonical_json()) == result
        assert load_detection_context(connection, snapshot.event_id) == snapshot
        assert (
            evaluate_outcome(connection, **arguments, algorithm=algorithm, events=(reference,))
            == result
        )


@pytest.mark.parametrize(
    "future,missing,reason",
    [
        (1, (), "required source minute"),
        (3, (16,), "required source minute"),
    ],
)
def test_insufficient_or_gapped_future_is_not_zero_filled(engine, future, missing, reason):
    with engine.begin() as connection:
        arguments, _ = setup_source(connection, definition(), future=future, missing=missing)

        def forbidden(_):
            pytest.fail("algorithm must not receive an incomplete window")

        result = evaluate_outcome(connection, **arguments, algorithm=forbidden)
        assert result.status is ResultStatus.INSUFFICIENT_DATA and result.value is None
        assert reason in result.reason


def test_same_bar_threshold_ambiguity_is_preserved(engine):
    with engine.begin() as connection:
        arguments, _ = setup_source(connection, definition())

        def thresholds(source):
            first = source.bars[0]
            reference = source.window.reference_price
            assert first.high >= reference + 5 and first.low <= reference - 5
            return OutcomeResult(
                ResultStatus.AMBIGUOUS, reason="both thresholds touched; OHLC ordering is unknown"
            )

        result = evaluate_outcome(connection, **arguments, algorithm=thresholds)
        assert result.status is ResultStatus.AMBIGUOUS and result.value is None


def test_ineligible_source_never_reaches_algorithm(engine):
    with engine.begin() as connection:
        arguments, _ = setup_source(connection, definition(eligible_event_kinds=("OTHER",)))

        def forbidden(_):
            pytest.fail("ineligible event reached the outcome algorithm")

        result = evaluate_outcome(connection, **arguments, algorithm=forbidden)
        assert result.status is ResultStatus.NOT_APPLICABLE and result.value is None


def test_definition_validation_decimal_config_and_stable_identity():
    with pytest.raises((ValidationError, OutcomeError), match="retrospective"):
        definition(reference_mode=ReferenceMode.FROM_EVENT_TIME)
    first = OutcomeSelection(
        outcome_id="fixture",
        outcome_version="1",
        parameters=(ConfigParameter(name="threshold", value=Decimal("1.00")),),
    )
    second = first.model_copy(
        update={"parameters": (ConfigParameter(name="threshold", value=Decimal("1")),)}
    )
    assert outcome_semantic_key("a" * 64, first) == outcome_semantic_key("a" * 64, second)
    plan = EvaluationPlan(
        detection_config_hash="a" * 64, context_schema_version="ctx", outcomes=(first,)
    )
    restored = EvaluationPlan.from_canonical_json(plan.canonical_json())
    assert restored.outcomes[0].parameters[0].value == Decimal(1)
    assert restored.canonical_json() == plan.canonical_json()
    with pytest.raises((ValidationError, ValueError)):
        EvaluationPlan.from_canonical_json('{"outcomes": []}')


def test_horizon_only_change_preserves_detection_and_x(engine):
    from uuid import UUID

    with engine.begin() as connection:
        first = drive(connection, outcome="fixture", outcome_specs=(HORIZON,))
        second = drive(
            connection,
            outcome="fixture",
            outcome_specs=(HORIZON,),
            outcome_values=(ConfigParameter(name="horizon", value=3),),
        )
        first_run = load_run_snapshot(connection, UUID(first[0].run_id))
        second_run = load_run_snapshot(connection, UUID(second[0].run_id))
        assert first_run.detection_config_hash == second_run.detection_config_hash
        assert first_run.evaluation_plan_hash != second_run.evaluation_plan_hash
        assert [item.semantic_hash for item in first[3]] == [
            item.semantic_hash for item in second[3]
        ]
        first_events = [
            item.event_semantic_key
            for record in first[1]._instances.values()
            for item in record.detector_events
        ]
        second_events = [
            item.event_semantic_key
            for record in second[1]._instances.values()
            for item in record.detector_events
        ]
        assert first_events == second_events


def test_callback_cannot_reference_undeclared_events_or_category(engine):
    with engine.begin() as connection:
        arguments, _ = setup_source(connection, definition(OutputType.CATEGORICAL))
        with pytest.raises(OutcomeError, match="category"):
            evaluate_outcome(
                connection,
                **arguments,
                algorithm=lambda source: OutcomeResult(
                    ResultStatus.AVAILABLE,
                    "UNDECLARED",
                ),
            )


def test_conflicting_recalculation_is_rejected(engine):
    with engine.begin() as connection:
        arguments, _ = setup_source(connection, definition())
        original = evaluate_outcome(
            connection,
            **arguments,
            algorithm=lambda source: OutcomeResult(
                ResultStatus.AVAILABLE,
                Decimal(2),
            ),
        )
        with pytest.raises(OutcomeError, match="immutable outcome"):
            evaluate_outcome(
                connection,
                **arguments,
                algorithm=lambda source: OutcomeResult(
                    ResultStatus.AVAILABLE,
                    Decimal(3),
                ),
            )
        assert load_outcome_observation(connection, original.observation_id) == original


@pytest.mark.parametrize("value", [float("nan"), 1.5, {}, object()])
def test_algorithm_result_rejects_untyped_values(value):
    with pytest.raises(OutcomeError, match="unsupported value type"):
        OutcomeResult(ResultStatus.AVAILABLE, value)


def test_algorithm_status_and_unavailable_value_are_explicit(engine):
    with pytest.raises(OutcomeError, match="ResultStatus"):
        OutcomeResult("AVAILABLE", Decimal(1))
    with engine.begin() as connection:
        arguments, _ = setup_source(connection, definition())
        with pytest.raises(ValidationError, match="no zero-filled value"):
            evaluate_outcome(
                connection,
                **arguments,
                algorithm=lambda source: OutcomeResult(
                    ResultStatus.INSUFFICIENT_DATA,
                    Decimal(0),
                    "missing future bars",
                ),
            )


def test_higher_timeframe_window_excludes_source_candle_ohlc(engine):
    with engine.begin() as connection:
        arguments, snapshot = setup_source(connection, definition(), timeframe=Timeframe.M5)

        def algorithm(source):
            assert source.window.reference_time == snapshot.available_at
            assert source.bars[0].timestamp == snapshot.available_at
            assert all(item.timestamp >= snapshot.available_at for item in source.bars)
            return OutcomeResult(ResultStatus.AVAILABLE, Decimal(1))

        result = evaluate_outcome(connection, **arguments, algorithm=algorithm)
        assert result.lineage.source_timeframe is Timeframe.M5
        assert result.window.reference_time > arguments["event"].detection_time


@pytest.mark.parametrize("boundary", [time(12, 16), time(12, 17)])
def test_window_does_not_cross_source_trading_day(engine, boundary):
    with engine.begin() as connection:
        arguments, _ = setup_source(connection, definition())
        arguments["calendar"] = replace(
            CALENDAR,
            trading_day_boundary=boundary,
            windows=(
                SessionWindow("day", time(12), boundary),
                SessionWindow("overnight", boundary, time(12)),
            ),
        )

        def forbidden(_):
            pytest.fail("cross-day outcome reached its algorithm")

        result = evaluate_outcome(connection, **arguments, algorithm=forbidden)
        assert result.status is ResultStatus.INSUFFICIENT_DATA
        assert "trading-day boundary" in result.reason
        assert result.window.observed_bars < result.window.horizon_bars


def test_scheduled_breaks_are_skipped_without_synthesizing_bars(engine):
    with engine.begin() as connection:
        arguments, _ = setup_source(connection, definition(), future=5, missing=(17, 18))
        arguments["calendar"] = replace(
            CALENDAR, breaks=(SessionWindow("break", time(12, 17), time(12, 19)),)
        )

        def algorithm(source):
            assert [item.timestamp for item in source.bars] == [
                bar(16).timestamp,
                bar(19).timestamp,
            ]
            return OutcomeResult(ResultStatus.AVAILABLE, Decimal(1))

        result = evaluate_outcome(connection, **arguments, algorithm=algorithm)
        assert result.window.end == bar(20).timestamp


def test_retrospective_mode_is_explicit_and_labelled(engine):
    with engine.begin() as connection:
        arguments, _ = setup_source(
            connection,
            definition(
                reference_mode=ReferenceMode.FROM_EVENT_TIME,
                retrospective_label="descriptive only",
            ),
        )
        result = evaluate_outcome(
            connection,
            **arguments,
            algorithm=lambda source: OutcomeResult(
                ResultStatus.AVAILABLE,
                Decimal(1),
            ),
        )
        assert result.window.reference_time == arguments["event"].event_time + timedelta(minutes=1)
        assert result.window.retrospective_label == "descriptive only"


def test_event_reference_order_and_window_are_enforced(engine):
    with engine.begin() as connection:
        arguments, snapshot = setup_source(connection, definition(OutputType.EVENT_REFERENCE))
        source = arguments["event"]
        market = OutcomeEventReference(
            semantic_ref="b" * 64,
            kind="market",
            dataset_revision_id=source.dataset_revision_id,
            instrument_id=source.instrument_id,
            timeframe=source.timeframe,
            detection_config_hash=source.detection_config_hash,
            event_time=source.event_time,
            detection_time=source.detection_time,
            available_at=snapshot.available_at,
            within_bar_ordinal=99,
        )
        later = market.model_copy(
            update={
                "kind": "detector",
                "semantic_ref": "c" * 64,
                "within_bar_ordinal": source.within_bar_ordinal + 1,
            }
        )
        outside = later.model_copy(update={"available_at": bar(99).timestamp})

        def algorithm(window):
            assert window.events == (later,)
            return OutcomeResult(ResultStatus.AVAILABLE, later)

        result = evaluate_outcome(
            connection, **arguments, algorithm=algorithm, events=(market, outside, later)
        )
        assert result.value == later
        with pytest.raises(OutcomeError, match="outside"):
            evaluate_outcome(
                connection,
                **arguments,
                events=(outside,),
                algorithm=lambda window: OutcomeResult(ResultStatus.AVAILABLE, outside),
            )


def test_snapshot_is_required_before_outcome_evaluation(engine):
    with engine.begin() as connection:
        arguments, snapshot = setup_source(connection, definition())
        connection.execute(
            text("DELETE FROM detection_context_snapshots WHERE event_id=:id"),
            {"id": snapshot.event_id},
        )
        with pytest.raises(OutcomeError, match="detection-time context"):
            evaluate_outcome(
                connection,
                **arguments,
                algorithm=lambda window: OutcomeResult(ResultStatus.AVAILABLE, Decimal(0)),
            )


def test_outcome_executor_cannot_claim_another_builds_lineage(engine):
    with engine.begin() as connection:
        arguments, _ = setup_source(connection, definition())
        with pytest.raises(OutcomeError, match="executor build differs"):
            evaluate_outcome(
                connection,
                **arguments,
                algorithm=lambda source: OutcomeResult(ResultStatus.AVAILABLE, Decimal(1)),
                revision_provider=lambda build: CodeVersion(
                    build,
                    "f" * 40,
                    False,
                    CodeCaptureStatus.AVAILABLE,
                    "fixture",
                ),
            )


def test_failed_predecessor_downgrade_preserves_newer_schema(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'atomic-migration.sqlite'}"
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(url)
    with engine.begin() as connection:
        setup_source(connection, definition())  # source context exists, no outcomes yet
    with pytest.raises(RuntimeError, match="detection-time context must be retained"):
        command.downgrade(Config("alembic.ini"), "20261105_15")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM outcome_observations")) == 0
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261106_19"
    engine.dispose()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_migration_immutability_and_used_downgrade(tmp_path, monkeypatch, backend, request):
    url = f"sqlite:///{tmp_path / 'outcomes.sqlite'}"
    if backend == "postgres":
        url = request.getfixturevalue("isolated_postgres_url")
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(url)
    with engine.begin() as connection:
        if backend == "sqlite":
            connection.execute(text("PRAGMA foreign_keys=ON"))
        arguments, _ = setup_source(connection, definition())
        result = evaluate_outcome(
            connection,
            **arguments,
            algorithm=lambda source: OutcomeResult(
                ResultStatus.AVAILABLE,
                Decimal(0),
            ),
        )
    for statement in (
        "UPDATE outcome_observations SET content_hash='changed'",
        "DELETE FROM outcome_observations",
    ):
        with engine.begin() as connection, pytest.raises(DatabaseError, match="immutable"):
            connection.execute(text(statement))
    with pytest.raises(RuntimeError, match="must be retained"):
        command.downgrade(Config("alembic.ini"), "20261106_16")
    with engine.connect() as connection:
        assert load_outcome_observation(connection, result.observation_id) == result
    engine.dispose()
