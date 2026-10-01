"""Execution-level SCRUM-61 evidence: one seeded replay through the pipeline.

Fixtures are provider-free: a small canonical bar series is registered through
the existing persistence helpers, then :class:`ReplayPipeline` drives the
SCRUM-63 cursor, SCRUM-77 market-state chain and SCRUM-80 detector runtime as
one causal chain over the persisted run. The scripted detector is the same
stand-in style as ``test_detector_runtime.py``; fixtures are rebuilt here so
every test module owns its own definitions.
"""

from __future__ import annotations

import ast
import io
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, update

from market_analysis.application.logging import (
    StructuredJsonFormatter,
    configure_logging,
)
from market_analysis.application.playback import PlaybackController, PlaybackMode
from market_analysis.application.replay_pipeline import (
    COMPONENT,
    DetectorEventFilter,
    ReplayPipeline,
    ReplayPipelineError,
)
from market_analysis.application.replay_run import create_replay_run, load_replay_context
from market_analysis.config import (
    ComponentSelection,
    ConfigParameter,
    DetectionAnalysisConfig,
    PatternSelection,
    detection_config_hash,
    resolve_detection_config,
)
from market_analysis.detection import (
    DetectorBinding,
    DetectorEvent,
    DetectorInput,
    DetectorOutput,
    DetectorRuntime,
    DetectorRuntimeError,
    TransitionIntent,
)
from market_analysis.domain import (
    BAR_CHECKSUM_VERSION,
    Bar,
    BarSequence,
    DatasetLineage,
    Instrument,
    ObservableBars,
    ReplayCursor,
    SessionWindow,
    SimulationClock,
    SimulationClockError,
    Timeframe,
    TradingCalendar,
    ValidationStatus,
    canonical_bar_checksum,
)
from market_analysis.indicators import MarketEventType, MarketStateAggregator
from market_analysis.patterns import (
    ConditionGroup,
    ContextFieldSpec,
    ParameterSpec,
    ParameterType,
    PatternDefinition,
    TransitionSpec,
)
from market_analysis.persistence.market_data import (
    DatasetMembership,
    DatasetRevision,
    load_bar_sequence,
    register_dataset_lineage,
    register_dataset_revision,
    register_instrument,
)
from market_analysis.persistence.replay_runs import (
    ReplayStatus,
    load_replay_run,
    transition_replay_run,
)
from market_analysis.persistence.runs import metadata, run_snapshots

START = datetime(2026, 1, 5, 12, tzinfo=UTC)
VISIBLE_START = START + timedelta(minutes=5)
SELECTED_END = START + timedelta(minutes=15)
REVISION = "rev-scrum-61"
BUILD_ID = "build-scrum-61"
CALENDAR_ID = "research-us30"
CALENDAR_VERSION = "fixture-v1"
WARMUP_BARS = 5

# Hand-reviewed real-chain series (UP direction), identical to the SCRUM-77/80
# fixture: EMA crosses from bar 5, swing confirmations and classifications on
# bars 6/8/10/12/14, the protected-high close break on bar 13, and the
# qualified leg start on bar 14 (its event_time cites bar 11).
TRIPLES = (
    ("129", "130", "100"),
    ("110", "129", "109"),
    ("80", "110", "79"),
    ("100", "101", "80"),
    ("120", "121", "99"),
    ("110", "120", "109"),
    ("90", "110", "89"),
    ("110", "111", "90"),
    ("140", "141", "109"),
    ("120", "140", "119"),
)


def bar(index: int, close: str = "120", high: str = "125", low: str = "115") -> Bar:
    return Bar(
        "US30", Timeframe.M1, START + timedelta(minutes=index),
        Decimal(close), Decimal(high), Decimal(low), Decimal(close),
    )


def chain_series() -> tuple[Bar, ...]:
    return tuple(bar(i, "100", "101", "99") for i in range(WARMUP_BARS)) + tuple(
        bar(i, close, high, low) for i, (close, high, low) in enumerate(TRIPLES, WARMUP_BARS)
    )


BARS = chain_series()
BAR_TIMESTAMPS = [item.timestamp for item in BARS]


def calendar() -> TradingCalendar:
    return TradingCalendar(
        calendar_id=CALENDAR_ID,
        version=CALENDAR_VERSION,
        provider="seed",
        account="local",
        instrument_id="US30",
        timezone_name="Europe/London",
        trading_day_boundary=time(0),
        windows=(
            SessionWindow("london", time(8), time(11)),
            SessionWindow("pre_us", time(11), time(14)),
            SessionWindow("us_open", time(14), time(17)),
            SessionWindow("late", time(17), time(20)),
        ),
    )


def calendar_resolver() -> Any:
    resolved = calendar()

    def resolve(calendar_id: str, version: str) -> TradingCalendar:
        if (calendar_id, version) != (CALENDAR_ID, CALENDAR_VERSION):
            raise AssertionError(f"unexpected calendar request {calendar_id}/{version}")
        return resolved

    return resolve


def component_selections() -> tuple[ComponentSelection, ...]:
    return (
        ComponentSelection(
            component_id="atr", component_version="1",
            parameters=(ConfigParameter(name="period", value=1),),
        ),
        ComponentSelection(
            component_id="ema", component_version="1",
            parameters=(ConfigParameter(name="period", value=3),),
        ),
        ComponentSelection(
            component_id="swing_point", component_version="1",
            parameters=(
                ConfigParameter(name="atr_period", value=1),
                ConfigParameter(name="reversal_atr_multiplier", value=Decimal("0.1")),
            ),
        ),
        ComponentSelection(component_id="swing_structure", component_version="1"),
        ComponentSelection(component_id="trend_leg", component_version="1"),
        ComponentSelection(
            component_id="trend_leg_qualification", component_version="1",
            parameters=(
                ConfigParameter(name="min_duration_bars", value=4),
                ConfigParameter(name="min_directional_move_points", value=Decimal("31")),
            ),
        ),
        ComponentSelection(component_id="range_state", component_version="1"),
    )


def config() -> DetectionAnalysisConfig:
    return DetectionAnalysisConfig(
        instrument_id="US30",
        timeframe=Timeframe.M1,
        calendar_id=CALENDAR_ID,
        components=component_selections(),
        patterns=(PatternSelection(pattern_id="trend-reversal", pattern_version="1"),),
    )


def reversal_definition() -> PatternDefinition:
    """SCRUM-83 stand-in: an EMA cross opens a candidate, a close break confirms."""
    return PatternDefinition(
        "trend-reversal", "1", "Trend reversal", "Reversal hypothesis",
        ("trend_leg", "ema"),
        ("opposing_ema_cross", "protected_swing_break"),
        (ParameterSpec("expiry_bars", ParameterType.INTEGER, 60, minimum=Decimal("1")),),
        ("idle", "candidate", "confirmed", "invalidated", "expired"),
        (
            TransitionSpec("idle", "candidate", "opposing_ema_cross"),
            TransitionSpec("candidate", "confirmed", "protected_swing_break"),
            TransitionSpec("candidate", "invalidated", "new_directional_extreme"),
            TransitionSpec("candidate", "expired", "expiry"),
        ),
        (
            ConditionGroup("candidate", ("opposing_ema_cross",)),
            ConditionGroup("confirmation", ("protected_swing_break",)),
            ConditionGroup("invalidation", ("new_directional_extreme",)),
            ConditionGroup("expiry", ("expiry",)),
        ),
        ("protected_swing_break", "new_directional_extreme", "expiry"),
        (
            ContextFieldSpec("protected_swing", "event_ref"),
            ContextFieldSpec("candidate_started_at", "datetime"),
        ),
        ("opposing_ema_cross", "protected_swing_break"),
    )


def definitions() -> dict[tuple[str, str], PatternDefinition]:
    definition = reversal_definition()
    return {definition.identity: definition}


class ScriptedDetector:
    """Reversal stand-in reacting only to canonical market events.

    Records every DetectorInput so tests can assert exactly one detector step
    per pipeline step; an optional failing bar exercises the latch contract.
    """

    def __init__(self, *, fail_on_completed_bar: int | None = None) -> None:
        self._reactions = {
            MarketEventType.EMA_CROSS: ("opposing_ema_cross",),
            MarketEventType.SWING_HIGH_CLOSE_BREAK: ("protected_swing_break",),
            MarketEventType.SWING_LOW_CLOSE_BREAK: ("protected_swing_break",),
        }
        self._fail_on_completed_bar = fail_on_completed_bar
        self.inputs: list[DetectorInput] = []

    def process_bar(self, bar_input: DetectorInput) -> DetectorOutput:
        self.inputs.append(bar_input)
        if bar_input.completed_bars == self._fail_on_completed_bar:
            raise RuntimeError("scripted detector failure")
        context = dict(bar_input.instance.context)
        end_state = bar_input.instance.state
        intents: list[TransitionIntent] = []
        for event in bar_input.market_events:
            triggers = self._reactions.get(event.event_type)
            if triggers is None:
                continue
            chain = _chain(bar_input.definition, end_state, triggers)
            if chain is None:
                continue
            for trigger, from_state, to_state in chain:
                context[f"{trigger}:ordinal"] = event.ordinal
                end_state = to_state
                intents.append(TransitionIntent(
                    pattern_id=bar_input.pattern_id,
                    pattern_version=bar_input.pattern_version,
                    instance_id=bar_input.instance.instance_id,
                    from_state=from_state,
                    to_state=to_state,
                    trigger_id=trigger,
                    event_time=event.event_time,
                    detection_time=bar_input.detection_time,
                    rationale={
                        "condition": trigger,
                        "source_event": event.event_type.value,
                        "source_ordinal": event.ordinal,
                    },
                ))
            break  # one transition set per bar; same-bar chains ride one event
        return DetectorOutput(
            instance=replace(bar_input.instance, state=end_state, context=context),
            transitions=tuple(intents),
        )

    def reset(self) -> None:
        self.inputs.clear()


def _chain(
    definition: PatternDefinition,
    state: str,
    triggers: tuple[str, ...],
) -> tuple[tuple[str, str, str], ...] | None:
    chain: list[tuple[str, str, str]] = []
    current = state
    for trigger in triggers:
        edge = next(
            (item for item in definition.transitions
             if item.from_state == current and item.trigger_id == trigger),
            None,
        )
        if edge is None:
            return None
        chain.append((trigger, edge.from_state, edge.to_state))
        current = edge.to_state
    return tuple(chain)


def reversal_detector(**kwargs: Any) -> ScriptedDetector:
    return ScriptedDetector(**kwargs)


def prepare(connection) -> None:
    register_instrument(connection, Instrument("US30", "US 30", CALENDAR_ID, 1, Decimal("1")))
    register_dataset_revision(connection, DatasetRevision(
        dataset_revision_id=REVISION, dataset_id="study", source_id="seed",
        provider="seed", retrieved_at=START, created_at=START,
        normalization_version="1", calendar_version=CALENDAR_VERSION,
        manifest_format_version="1", manifest_ref=f"datasets/{REVISION}/manifest.json",
        memberships=(DatasetMembership(
            "US30", Timeframe.M1, START, SELECTED_END, len(BARS),
        ),),
    ))
    register_dataset_lineage(connection, DatasetLineage(
        dataset_revision_id=REVISION, source_dataset_id="seed-61",
        instrument_id="US30", timeframe=Timeframe.M1,
        requested_start=START, requested_end=SELECTED_END,
        actual_start=START, actual_end=SELECTED_END,
        bar_count=len(BARS), acquired_at=START,
        validation_status=ValidationStatus.PASS,
        provider_request_json='{"dataset":"seed-61"}',
        source_checksum="a" * 64, canonical_checksum=canonical_bar_checksum(BARS),
        checksum_version=BAR_CHECKSUM_VERSION, dataset_format_version="parquet-v1",
    ))


def create_run(
    connection,
    run_id: UUID,
    *,
    selected_start: datetime = VISIBLE_START,
    selected_end: datetime = SELECTED_END,
) -> None:
    create_replay_run(
        connection, run_id=run_id, dataset_revision_id=REVISION,
        detection_config=config(), selected_start=selected_start,
        selected_end=selected_end, created_at=START,
        build_id=BUILD_ID, pattern_definitions=definitions(),
    )


def build_pipeline(
    connection,
    run_id: UUID,
    detector: ScriptedDetector | None = None,
) -> tuple[ReplayPipeline, ScriptedDetector]:
    scripted = reversal_detector() if detector is None else detector
    pipeline = ReplayPipeline.from_run(
        connection,
        run_id,
        bars=BARS,
        bindings_factory=lambda: [DetectorBinding(reversal_definition(), scripted)],
        pattern_definitions=definitions(),
        calendar_resolver=calendar_resolver(),
    )
    return pipeline, scripted


def transitions(events: tuple[DetectorEvent, ...]) -> list[tuple[int, str, str, str]]:
    return [
        (event.sequence, event.from_state, event.to_state, event.trigger_id)
        for event in events
    ]


def transitions_in_runtime(pipeline: ReplayPipeline) -> list[tuple[int, str, str, str]]:
    return transitions(pipeline.runtime.events)


def run_to_completion(connection, run_id: UUID) -> ReplayPipeline:
    pipeline, _ = build_pipeline(connection, run_id)
    completion = pipeline.run_to_completion(connection, at=SELECTED_END)
    assert completion.record.status is ReplayStatus.COMPLETED
    return pipeline


@contextmanager
def captured_log() -> Iterator[list[dict[str, object]]]:
    """Capture structured application records as parsed JSON payloads."""
    base = configure_logging(enable_file=False)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredJsonFormatter())
    base.addHandler(handler)
    records: list[dict[str, object]] = []
    try:
        yield records
    finally:
        base.removeHandler(handler)
        records.extend(
            json.loads(line) for line in stream.getvalue().splitlines() if line.strip()
        )


def test_full_execution_completes_with_warmup_events_and_causality() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    with captured_log():
        with engine.begin() as connection:
            prepare(connection)
            create_run(connection, run_id)
            pipeline = run_to_completion(connection, run_id)

            lifecycle = load_replay_run(connection, run_id)
            assert lifecycle is not None
            assert lifecycle.status is ReplayStatus.COMPLETED
            assert lifecycle.completed_at == SELECTED_END
            assert lifecycle.cursor_index == len(BARS) - 1
            assert pipeline.cursor_index == lifecycle.cursor_index

            # exactly one step per completed bar, in canonical order
            steps = pipeline.steps
            assert [step.index for step in steps] == list(range(len(BARS)))
            assert all(step.result.frame.completed_bars == step.index + 1 for step in steps)

            # warm-up precedes the visible interval
            warmup = [step for step in steps if not step.is_visible]
            visible = [step for step in steps if step.is_visible]
            assert len(warmup) == WARMUP_BARS > 0
            assert all(step.view.timestamp < VISIBLE_START for step in warmup)
            assert all(step.view.timestamp >= VISIBLE_START for step in visible)

            # DetectorEvents flow through the shared runtime with real transitions
            events = pipeline.runtime.events
            assert {event.pattern_id for event in events} == {"trend-reversal"}
            assert transitions_in_runtime(pipeline) == [
                (0, "idle", "candidate", "opposing_ema_cross"),
                (1, "candidate", "confirmed", "protected_swing_break"),
            ]
            producing = [
                step.index for step in steps if step.result.events
            ]
            assert [event.detection_time for event in events] == [
                BAR_TIMESTAMPS[index] for index in producing
            ]

            # no future data: every event cites only observable past bars
            for position, step in enumerate(steps):
                bar_timestamp = step.view.current_bar.timestamp
                for market_event in step.result.frame.market_events_this_bar:
                    assert market_event.detection_time <= bar_timestamp
                    assert market_event.event_time <= market_event.detection_time
                for event in step.result.events:
                    assert isinstance(event, DetectorEvent)
                    assert event.detection_time == bar_timestamp
                    assert event.event_time in BAR_TIMESTAMPS[: position + 1]
                    assert event.run_id == str(run_id)
                    assert event.dataset_revision_id == REVISION
                    assert event.instrument_id == "US30"
                    assert event.detection_config_hash == (
                        pipeline.runtime.detection_config_hash
                    )
            assert producing == [5, 13]  # the EMA cross and the protected-high break

            # a completed run is no longer steppable
            with pytest.raises(ReplayPipelineError, match="not running"):
                pipeline.step(connection)
    engine.dispose()


def test_step_processes_exactly_one_completed_bar() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    with engine.begin() as connection:
        prepare(connection)
        create_run(connection, run_id)
        pipeline, detector = build_pipeline(connection, run_id)
        pipeline.start(connection, at=START)
        lifecycle = load_replay_run(connection, run_id)
        assert lifecycle is not None and lifecycle.status is ReplayStatus.RUNNING

        first = pipeline.step(connection)
        assert first.index == 0 and first.is_visible is False
        assert len(detector.inputs) == 1
        assert detector.inputs[0].frame is first.result.frame
        assert pipeline.cursor_index == 0
        lifecycle = load_replay_run(connection, run_id)
        assert lifecycle is not None and lifecycle.cursor_index == 0
        assert len(pipeline.steps) == 1

        second = pipeline.step(connection)
        assert second.index == 1
        assert len(detector.inputs) == 2
        assert pipeline.cursor_index == 1
        lifecycle = load_replay_run(connection, run_id)
        assert lifecycle is not None and lifecycle.cursor_index == 1
    engine.dispose()


def test_fresh_runs_over_the_same_data_are_byte_identical() -> None:
    """SCRUM-61 reset semantics: a new run id with fresh state, never a rewrite."""
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    prepare_replays: list[dict[str, object]] = []
    for _ in range(2):
        run_id = uuid4()
        with engine.begin() as connection:
            prepare(connection)
            create_run(connection, run_id)
            pipeline = run_to_completion(connection, run_id)
            prepare_replays.append({
                "events": [
                    {key: value for key, value in event.to_canonical_dict().items()
                     if key != "run_id"}
                    for event in pipeline.runtime.events
                ],
                "instances": pipeline.runtime.instances,
                "frames": [
                    _without_run_id(json.loads(step.result.frame.debug_json()))
                    for step in pipeline.steps
                ],
                "binding_fingerprint": pipeline.runtime.binding_fingerprint,
                "config_hash": pipeline.runtime.detection_config_hash,
            })
    first, second = prepare_replays
    assert first["events"] == second["events"] != []
    assert first["instances"] == second["instances"]
    assert first["frames"] == second["frames"]
    assert first["binding_fingerprint"] == second["binding_fingerprint"]
    assert first["config_hash"] == second["config_hash"]
    engine.dispose()


def _without_run_id(payload: object) -> object:
    """Drop run-scoped identity so two fresh runs compare byte-identically.

    Every other field in the frame payloads — component values, availability,
    versions, lineages, market events — must be equal across runs; only the
    ``run_id`` stamp (top level and inside component/evidence lineage) differs
    because each fresh run owns a new identity.
    """
    if isinstance(payload, dict):
        return {
            key: _without_run_id(value)
            for key, value in payload.items()
            if key != "run_id"
        }
    if isinstance(payload, list):
        return [_without_run_id(item) for item in payload]
    return payload


class CaptureRuntimePipeline:
    """Ephemeral real-chain adapter for causal seek and speed parity fixtures."""

    def __init__(self, run_id: str) -> None:
        resolved = resolve_detection_config(config(), pattern_definitions=definitions())
        pinned = DetectionAnalysisConfig.from_canonical_json(resolved.canonical_json())
        self.runtime = DetectorRuntime(
            pinned,
            [DetectorBinding(reversal_definition(), reversal_detector())],
            run_id=run_id,
            dataset_revision_id=REVISION,
            calendar=calendar(),
            pinned_calendar_version=CALENDAR_VERSION,
            pinned_config_hash=detection_config_hash(
                pinned, pattern_definitions=definitions()
            ),
        )
        self.frames: list[str] = []

    def process_bar(self, view: ObservableBars) -> None:
        self.frames.append(self.runtime.process_bar(view.current_bar).frame.debug_json())

    def reset(self) -> None:
        self.runtime.reset()
        self.frames.clear()


def make_ephemeral_cursor(
    sequence: BarSequence, run_id: str
) -> tuple[ReplayCursor, CaptureRuntimePipeline]:
    pipeline = CaptureRuntimePipeline(run_id)
    return (
        ReplayCursor(
            SimulationClock(sequence, selected_start=VISIBLE_START, selected_end=SELECTED_END),
            pipeline,
        ),
        pipeline,
    )


def state_hashes(pipeline: CaptureRuntimePipeline) -> tuple[str, ...]:
    return tuple(sha256(frame.encode("utf-8")).hexdigest() for frame in pipeline.frames)


def test_seek_rebuilds_real_market_state_and_detector_events_from_origin() -> None:
    """Seek parity includes warm-up, canonical frames, and actual detector output."""
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = str(uuid4())

    with engine.begin() as connection:
        prepare(connection)
        sequence = load_bar_sequence(connection, REVISION, "US30", Timeframe.M1, BARS)

    seek, actual = make_ephemeral_cursor(sequence, run_id)
    seek.run_to_end()
    for target in (0, 4, 5, 13, 8, 14):
        if target == 8:
            view = seek.seek_to_time(BAR_TIMESTAMPS[target])
        else:
            view = seek.seek_to_index(target)
        uninterrupted, expected = make_ephemeral_cursor(sequence, run_id)
        uninterrupted.step_n(target + 1)
        assert view is not None and view.index == target
        assert view.is_visible == (target >= WARMUP_BARS)
        assert seek.timestamp == uninterrupted.timestamp
        assert actual.frames == expected.frames
        assert state_hashes(actual) == state_hashes(expected)
        assert actual.runtime.instances == expected.runtime.instances
        assert actual.runtime.events == expected.runtime.events
        assert all(event.detection_time <= view.timestamp for event in actual.runtime.events)

    seek.seek_to_index(5)
    seek.run_to_end()
    uninterrupted, expected = make_ephemeral_cursor(sequence, run_id)
    uninterrupted.run_to_end()
    assert actual.frames == expected.frames
    assert state_hashes(actual) == state_hashes(expected)
    assert actual.runtime.events == expected.runtime.events
    assert actual.runtime.events  # the fixture exercises real detector transitions
    seek.seek_to_index(-1)
    assert seek.index == -1 and actual.frames == [] and actual.runtime.events == ()
    engine.dispose()


def test_playback_speeds_preserve_real_market_state_and_detector_event_parity() -> None:
    """Manual, paced and maximum scheduling share one deterministic chain."""
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        prepare(connection)
        sequence = load_bar_sequence(connection, REVISION, "US30", Timeframe.M1, BARS)
    run_id = str(uuid4())

    manual_cursor, manual = make_ephemeral_cursor(sequence, run_id)
    manual_control = PlaybackController(manual_cursor)
    while manual_cursor.has_next:
        manual_control.step_one()
    assert manual.runtime.events

    slow_cursor, slow = make_ephemeral_cursor(sequence, run_id)
    slow_control = PlaybackController(slow_cursor)
    slow_control.play(Decimal("0.5"))
    for _ in BARS:
        assert slow_control.tick(timedelta(seconds=2)) == 1

    fast_cursor, fast = make_ephemeral_cursor(sequence, run_id)
    fast_control = PlaybackController(fast_cursor)
    fast_control.play(Decimal("3"))
    assert [fast_control.tick(timedelta(seconds=1)) for _ in range(5)] == [3] * 5

    maximum_cursor, maximum = make_ephemeral_cursor(sequence, run_id)
    maximum_control = PlaybackController(maximum_cursor)
    maximum_control.play_maximum()
    assert maximum_control.tick() == len(BARS)

    for control, pipeline in (
        (slow_control, slow), (fast_control, fast), (maximum_control, maximum)
    ):
        assert control.mode is PlaybackMode.PAUSED
        assert control.cursor.index == manual_cursor.index == len(BARS) - 1
        assert state_hashes(pipeline) == state_hashes(manual)
        assert pipeline.runtime.instances == manual.runtime.instances
        assert pipeline.runtime.events == manual.runtime.events
    engine.dispose()


def _regression_trace(pipeline: ReplayPipeline) -> tuple[tuple[object, ...], ...]:
    """Small, readable golden over the warm-up edge and meaningful transitions."""
    selected = (0, 4, 5, 6, 13, 14)
    return tuple(
        (
            step.index,
            step.is_visible,
            str(step.result.frame.components["ema"]["ema"]),
            str(step.result.frame.components["atr"]["atr"]),
            step.result.frame.availability["trend_leg"],
            tuple(
                (event.ordinal, event.event_type.value,
                 (event.event_time - START) // timedelta(minutes=1))
                for event in step.result.frame.market_events_this_bar
            ),
            tuple(
                (event.sequence, event.from_state, event.to_state, event.trigger_id)
                for event in step.result.events
            ),
        )
        for step in pipeline.steps if step.index in selected
    )


def test_replay_regression_golden_trace_and_execution_mode_matrix() -> None:
    """A common drift fails the golden; mode-specific drift fails full-frame parity."""
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    with engine.begin() as connection:
        prepare(connection)
        create_run(connection, run_id)
        sequence = load_bar_sequence(connection, REVISION, "US30", Timeframe.M1, BARS)

        persisted = run_to_completion(connection, run_id)
        assert _regression_trace(persisted) == (
            (0, False, "None", "2", "NO_ACTIVE_LEG", (), ()),
            (4, False, "100.00", "2", "NO_ACTIVE_LEG", (), ()),
            (5, True, "114.500", "30", "NO_ACTIVE_LEG",
             ((0, "EMA_CROSS", 5),),
             ((0, "idle", "candidate", "opposing_ema_cross"),)),
            (6, True, "112.2500", "20", "NO_ACTIVE_LEG",
             ((0, "SWING_POINT_CONFIRMED", 5),
              (1, "SWING_STRUCTURE_CLASSIFIED", 5), (2, "EMA_CROSS", 6)), ()),
            (13, True, "122.43945312500", "32", "NO_ACTIVE_LEG",
             ((0, "SWING_HIGH_CLOSE_BREAK", 13),),
             ((1, "candidate", "confirmed", "protected_swing_break"),)),
            (14, True, "121.219726562500", "21", "ACTIVE",
             ((0, "SWING_POINT_CONFIRMED", 13),
              (1, "SWING_STRUCTURE_CLASSIFIED", 13),
              (2, "EMA_CROSS", 14), (3, "TREND_LEG_STARTED", 11),
              (4, "TREND_LEG_QUALIFIED", 14)), ()),
        )
        baseline_frames = tuple(step.result.frame.debug_json() for step in persisted.steps)
        baseline_events = persisted.runtime.events
        baseline_instances = persisted.runtime.instances
        baseline_occurrences = persisted.runtime.occurrences

        for mode in ("step_one", "step_n", "run_to_end", "paced_slow", "paced_fast",
                     "maximum", "seek"):
            cursor, analytical = make_ephemeral_cursor(sequence, str(run_id))
            if mode == "step_one":
                while cursor.has_next:
                    cursor.step_one()
            elif mode == "step_n":
                for count in (2, 3, 1, 9):
                    cursor.step_n(count)
            elif mode == "run_to_end":
                assert cursor.run_to_end() == len(BARS)
            elif mode == "paced_slow":
                control = PlaybackController(cursor)
                control.play(Decimal("0.5"))
                for _ in BARS:
                    assert control.tick(timedelta(seconds=2)) == 1
            elif mode == "paced_fast":
                control = PlaybackController(cursor)
                control.play(Decimal("3"))
                for _ in range(5):
                    assert control.tick(timedelta(seconds=1)) == 3
            elif mode == "maximum":
                control = PlaybackController(cursor)
                control.play_maximum()
                assert control.tick() == len(BARS)
            else:
                cursor.run_to_end()
                assert cursor.seek_to_index(4) is not None  # warm-up
                assert tuple(analytical.frames) == baseline_frames[:5]
                assert analytical.runtime.events == ()
                assert cursor.seek_to_time(BAR_TIMESTAMPS[13]).index == 13
                assert tuple(analytical.frames) == baseline_frames[:14]
                assert analytical.runtime.events == baseline_events
                assert cursor.seek_to_index(8) is not None  # backward again
                assert tuple(analytical.frames) == baseline_frames[:9]
                assert analytical.runtime.events == baseline_events[:1]
                assert cursor.run_to_end() == 6
            assert cursor.index == len(BARS) - 1 and not cursor.has_next, mode
            assert tuple(analytical.frames) == baseline_frames, mode
            assert analytical.runtime.events == baseline_events, mode
            assert analytical.runtime.instances == baseline_instances, mode
            assert analytical.runtime.occurrences == baseline_occurrences, mode

        fresh_id = uuid4()
        create_run(connection, fresh_id)
        fresh = run_to_completion(connection, fresh_id)
        assert [
            _without_run_id(json.loads(step.result.frame.debug_json()))
            for step in fresh.steps
        ] == [
            _without_run_id(json.loads(frame)) for frame in baseline_frames
        ]
        assert [
            {key: value for key, value in event.to_canonical_dict().items()
             if key != "run_id"}
            for event in fresh.runtime.events
        ] == [
            {key: value for key, value in event.to_canonical_dict().items()
             if key != "run_id"}
            for event in baseline_events
        ]
        assert fresh.runtime.instances == baseline_instances
        assert fresh.runtime.occurrences == baseline_occurrences
    engine.dispose()


def test_replay_regression_rejects_future_access_at_every_step_and_after_seek() -> None:
    """A future getter or retroactively expanding old view fails in normal CI."""
    class FutureProbePipeline(CaptureRuntimePipeline):
        def __init__(self, run_id: str) -> None:
            super().__init__(run_id)
            self.views: list[ObservableBars] = []

        def process_bar(self, view: ObservableBars) -> None:
            self.views.append(view)
            assert view.history() == BARS[:view.index + 1]
            assert view.bar_at(view.index) == BARS[view.index]
            if view.index + 1 < len(BARS):
                with pytest.raises(SimulationClockError, match="future"):
                    view.bar_at(view.index + 1)
                with pytest.raises(SimulationClockError, match="future"):
                    view.bar_at_time(BAR_TIMESTAMPS[view.index + 1])
            super().process_bar(view)

        def reset(self) -> None:
            super().reset()
            self.views.clear()

    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        prepare(connection)
        sequence = load_bar_sequence(connection, REVISION, "US30", Timeframe.M1, BARS)
    analytical = FutureProbePipeline(str(uuid4()))
    cursor = ReplayCursor(
        SimulationClock(sequence, selected_start=VISIBLE_START, selected_end=SELECTED_END),
        analytical,
    )
    early = cursor.step_one()
    assert not early.is_visible
    cursor.run_to_end()
    assert early.history() == (BARS[0],)
    with pytest.raises(SimulationClockError, match="future"):
        early.bar_at(1)
    cursor.seek_to_index(5)
    assert [view.index for view in analytical.views] == list(range(6))
    assert early.history() == (BARS[0],)
    with pytest.raises(SimulationClockError, match="future"):
        early.bar_at_time(BAR_TIMESTAMPS[1])
    detector_inputs = analytical.runtime.bindings[0].detector.inputs
    assert [bar_input.frame.bar for bar_input in detector_inputs] == list(BARS[:6])
    assert all(
        event.detection_time <= bar_input.bar_timestamp
        for bar_input in detector_inputs
        for event in bar_input.market_events
    )
    engine.dispose()


def test_run_until_stops_on_each_visible_event_then_resumes_without_skipping() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id, uninterrupted_id = uuid4(), uuid4()
    with engine.begin() as connection:
        prepare(connection)
        create_run(connection, run_id)
        pipeline, _ = build_pipeline(connection, run_id)

        first = pipeline.run_until_event(connection, at=START)
        assert first.stopped_on_event and first.record.status is ReplayStatus.PAUSED
        assert first.processed_bars == 6 and first.record.cursor_index == 5
        assert first.stop_step is not None and first.stop_step.index == 5
        assert first.matched_event is not None
        assert first.matched_event.to_state == "candidate"
        assert pipeline.cursor_index == load_replay_run(connection, run_id).cursor_index == 5

        second = pipeline.run_until_event(connection, at=START)
        assert second.stopped_on_event and second.record.status is ReplayStatus.PAUSED
        assert second.processed_bars == 8 and second.record.cursor_index == 13
        assert second.matched_event is not None
        assert second.matched_event.to_state == "confirmed"
        assert pipeline.cursor_index == load_replay_run(connection, run_id).cursor_index == 13

        completed = pipeline.run_until_event(connection, at=SELECTED_END)
        assert not completed.stopped_on_event
        assert completed.stop_step is None and completed.matched_event is None
        assert completed.processed_bars == 1
        assert completed.record.status is ReplayStatus.COMPLETED
        assert completed.record.cursor_index == len(BARS) - 1

        create_run(connection, uninterrupted_id)
        uninterrupted = run_to_completion(connection, uninterrupted_id)
        assert transitions_in_runtime(pipeline) == transitions_in_runtime(uninterrupted)
        assert [
            _without_run_id(json.loads(step.result.frame.debug_json()))
            for step in pipeline.steps
        ] == [
            _without_run_id(json.loads(step.result.frame.debug_json()))
            for step in uninterrupted.steps
        ]
        assert [step.index for step in pipeline.steps] == list(range(len(BARS)))
    engine.dispose()


def test_run_until_filter_skips_nonmatching_events_and_no_match_completes() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        prepare(connection)
        selected_id = uuid4()
        create_run(connection, selected_id)
        selected, _ = build_pipeline(connection, selected_id)
        result = selected.run_until_event(
            connection,
            DetectorEventFilter(
                pattern_id="trend-reversal", trigger_id="protected_swing_break",
                from_state="candidate", to_state="confirmed",
            ),
            at=START,
        )
        assert result.stopped_on_event
        assert result.record.cursor_index == 13 and result.processed_bars == 14
        assert result.matched_event is selected.runtime.events[1]
        assert len(selected.runtime.events) == 2  # earlier nonmatching candidate was retained

        no_match_id = uuid4()
        create_run(connection, no_match_id)
        no_match, _ = build_pipeline(connection, no_match_id)
        completed = no_match.run_until_event(
            connection, DetectorEventFilter(pattern_id="another-pattern"), at=SELECTED_END
        )
        assert completed.record.status is ReplayStatus.COMPLETED
        assert completed.processed_bars == len(BARS)
        assert completed.matched_event is None
        assert transitions_in_runtime(no_match) == transitions_in_runtime(selected)
    engine.dispose()


def test_run_until_processes_warmup_but_does_not_stop_on_warmup_event() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    with engine.begin() as connection:
        prepare(connection)
        create_run(connection, run_id, selected_start=START + timedelta(minutes=10))
        pipeline, _ = build_pipeline(connection, run_id)
        result = pipeline.run_until_event(connection, at=START)
        assert result.stopped_on_event and result.record.cursor_index == 13
        assert result.matched_event is not None
        assert result.matched_event.to_state == "confirmed"
        assert pipeline.warmup_bars == 10 and pipeline.visible_bars == 4
        assert pipeline.steps[5].result.events[0].to_state == "candidate"
        assert not pipeline.steps[5].is_visible
    engine.dispose()


def test_run_until_matches_second_of_multiple_events_on_one_bar() -> None:
    class DoubleTransitionDetector(ScriptedDetector):
        def __init__(self) -> None:
            super().__init__()
            self._reactions[MarketEventType.EMA_CROSS] = (
                "opposing_ema_cross", "protected_swing_break"
            )

    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        prepare(connection)
        run_id = uuid4()
        definition = replace(
            reversal_definition(),
            same_bar_chains=(("opposing_ema_cross", "protected_swing_break"),),
        )
        custom_definitions = {definition.identity: definition}
        create_replay_run(
            connection, run_id=run_id, dataset_revision_id=REVISION,
            detection_config=config(), selected_start=VISIBLE_START,
            selected_end=SELECTED_END, created_at=START, build_id=BUILD_ID,
            pattern_definitions=custom_definitions,
        )
        pipeline = ReplayPipeline.from_run(
            connection, run_id, bars=BARS,
            bindings_factory=lambda: [DetectorBinding(definition, DoubleTransitionDetector())],
            pattern_definitions=custom_definitions,
            calendar_resolver=calendar_resolver(),
        )
        result = pipeline.run_until_event(
            connection, DetectorEventFilter(to_state="confirmed"), at=START
        )
        assert result.stopped_on_event and result.record.cursor_index == 5
        assert result.stop_step is not None
        assert [event.to_state for event in result.stop_step.result.events] == [
            "candidate", "confirmed"
        ]
        assert result.matched_event is result.stop_step.result.events[1]
        assert pipeline.runtime.events == result.stop_step.result.events
    engine.dispose()


def test_run_until_final_bar_match_pauses_then_next_call_completes() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    with engine.begin() as connection:
        prepare(connection)
        create_run(connection, run_id, selected_end=START + timedelta(minutes=14))
        pipeline, _ = build_pipeline(connection, run_id)
        stopped = pipeline.run_until_event(
            connection, DetectorEventFilter(to_state="confirmed"), at=START
        )
        assert stopped.record.status is ReplayStatus.PAUSED
        assert stopped.record.cursor_index == 13 and not pipeline.has_next
        completed = pipeline.run_until_event(connection, at=SELECTED_END)
        assert completed.record.status is ReplayStatus.COMPLETED
        assert completed.processed_bars == 0 and completed.matched_event is None
        assert len(pipeline.steps) == 14
    engine.dispose()


def test_run_until_rejects_invalid_filter_before_starting() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    with engine.begin() as connection:
        prepare(connection)
        create_run(connection, run_id)
        pipeline, _ = build_pipeline(connection, run_id)
        with pytest.raises(ReplayPipelineError, match="DetectorEventFilter"):
            pipeline.run_until_event(connection, object(), at=START)  # type: ignore[arg-type]
        with pytest.raises(ReplayPipelineError, match="pattern_id"):
            DetectorEventFilter(pattern_id=" ")
        assert pipeline.steps == () and pipeline.cursor_index == -1
        assert load_replay_run(connection, run_id).status is ReplayStatus.CREATED
    engine.dispose()


def test_run_until_analytical_failure_latches_and_marks_run_failed() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    with engine.begin() as connection:
        prepare(connection)
        create_run(connection, run_id)
        pipeline, _ = build_pipeline(connection, run_id, reversal_detector(fail_on_completed_bar=2))
        with pytest.raises(DetectorRuntimeError, match="scripted detector failure"):
            pipeline.run_until_event(connection, at=START)
        record = load_replay_run(connection, run_id)
        assert record is not None and record.status is ReplayStatus.FAILED
        assert record.cursor_index == 0 and pipeline.cursor_index == 1
        with pytest.raises(ReplayPipelineError, match="latched"):
            pipeline.run_until_event(connection, at=START)
    engine.dispose()


def test_run_until_pause_write_failure_latches_at_the_matching_bar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_analysis.application import replay_pipeline

    original_transition = replay_pipeline.transition_replay_run

    def pause_unavailable(connection, run_id, target, **kwargs):
        if target is ReplayStatus.PAUSED:
            raise RuntimeError("pause write failed")
        return original_transition(connection, run_id, target, **kwargs)

    monkeypatch.setattr(replay_pipeline, "transition_replay_run", pause_unavailable)
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    with engine.begin() as connection:
        prepare(connection)
        create_run(connection, run_id)
        pipeline, _ = build_pipeline(connection, run_id)
        with pytest.raises(RuntimeError, match="pause write failed"):
            pipeline.run_until_event(connection, at=START)
        record = load_replay_run(connection, run_id)
        assert record is not None and record.status is ReplayStatus.FAILED
        assert record.cursor_index == pipeline.cursor_index == 5
        assert pipeline.failure_context is not None
        with pytest.raises(ReplayPipelineError, match="latched"):
            pipeline.run_until_event(connection, at=START)
    engine.dispose()


def test_lineage_binding_survives_reload_through_the_pipeline_path() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    with engine.begin() as connection:
        prepare(connection)
        create_run(connection, run_id)
        pipeline = run_to_completion(connection, run_id)

        reloaded = load_replay_context(connection, run_id, pattern_definitions=definitions())
        assert reloaded is not None
        assert reloaded.snapshot.dataset_revision_id == REVISION
        assert reloaded.snapshot.calendar_version == CALENDAR_VERSION
        assert reloaded.snapshot.build_id == BUILD_ID
        assert reloaded.snapshot.detection_config_hash == (
            pipeline.runtime.detection_config_hash
        )
        assert reloaded.lineage.canonical_checksum == canonical_bar_checksum(BARS)
        assert reloaded.detection_config == pipeline.detection_config

        # Metadata reload is supported; rebuilding analytical state at an
        # advanced cursor requires a new run, never an implicit restore.
        with pytest.raises(ReplayPipelineError, match="new run id"):
            build_pipeline(connection, run_id)
    engine.dispose()


def test_detector_failure_keeps_cursor_clock_and_runtime_consistent() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    detector = reversal_detector(fail_on_completed_bar=WARMUP_BARS + 9)  # dataset bar 13
    with captured_log() as records:
        with engine.begin() as connection:
            prepare(connection)
            create_run(connection, run_id)
            pipeline, _ = build_pipeline(connection, run_id, detector)
            pipeline.start(connection, at=START)
            for _ in range(13):
                pipeline.step(connection)
            assert pipeline.cursor_index == 12
            committed = pipeline.runtime.events

            with pytest.raises(DetectorRuntimeError, match="scripted detector failure"):
                pipeline.step(connection)

            # The failed bar is consumed but not committed: DB cursor at
            # k-1, clock/aggregator at k, detector events at the prior bar.
            lifecycle = load_replay_run(connection, run_id)
            assert lifecycle is not None and lifecycle.cursor_index == 12
            assert lifecycle.status is ReplayStatus.FAILED
            assert lifecycle.failure_reason == pipeline.failure_context
            assert pipeline.cursor_index == 13
            assert pipeline.runtime.events == committed
            assert pipeline.runtime.aggregator.completed_bars == 14
            assert len(detector.inputs) == 14  # the failed detector step ran once
            assert pipeline.failure_context is not None
            assert "trend-reversal@1" in pipeline.failure_context

            # a subsequent step is rejected by the latch and advances nothing
            with pytest.raises(ReplayPipelineError, match="latched"):
                pipeline.step(connection)
            lifecycle = load_replay_run(connection, run_id)
            assert lifecycle is not None and lifecycle.cursor_index == 12

            # SCRUM-61 reset semantics: no in-place reset of a persisted run
            with pytest.raises(ReplayPipelineError, match="new run id"):
                pipeline.reset()
            lifecycle = load_replay_run(connection, run_id)
            assert lifecycle is not None and lifecycle.cursor_index == 12

    error_records = [item for item in records if item["severity"] == "ERROR"]
    assert len(error_records) == 1
    record = error_records[0]
    assert record["run_id"] == str(run_id)
    assert record["dataset_id"] == REVISION
    assert record["instrument"] == "US30"
    assert record["component"] == COMPONENT
    assert record["detector_id"] == "trend-reversal@1"
    assert record["detector_instance_id"] == "trend-reversal"
    assert record["bar_index"] == 13
    assert "scripted detector failure" in str(record["error"])
    engine.dispose()


def test_execution_records_carry_run_instrument_and_component_context() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    with captured_log() as records:
        with engine.begin() as connection:
            prepare(connection)
            create_run(connection, run_id)
            pipeline = run_to_completion(connection, run_id)

    started = [item for item in records if item["message"] == "replay run started"]
    assert len(started) == 1
    assert started[0]["run_id"] == str(run_id)
    assert started[0]["severity"] == "INFO"

    execution = [item for item in records if item["message"] == "replay bar processed"]
    assert len(execution) == len(BARS)
    assert [item["bar_index"] for item in execution] == list(range(len(BARS)))
    assert [item["is_visible"] for item in execution] == (
        [False] * WARMUP_BARS + [True] * (len(BARS) - WARMUP_BARS)
    )
    sample = execution[-1]
    assert sample["severity"] == "INFO"
    assert sample["run_id"] == str(run_id)
    assert sample["dataset_id"] == REVISION
    assert sample["instrument"] == "US30"
    assert sample["component"] == COMPONENT
    assert sample["build_id"] == BUILD_ID
    assert sample["phase"] == "execution"
    assert sample["detectors"] == ["trend-reversal@1:trend-reversal"]
    assert sample["components"] == [
        "atr", "ema", "range_state", "session", "swing_point", "swing_structure",
        "trend_leg", "trend_leg_qualification",
    ]
    assert sample["binding_fingerprint"] == pipeline.runtime.binding_fingerprint
    assert sample["completed_bars"] == len(BARS)
    engine.dispose()


def test_pipeline_construction_rejects_a_changed_dataset_content() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    with engine.begin() as connection:
        prepare(connection)
        create_run(connection, run_id)
        tampered = tuple(
            replace(item, close=Decimal("100.5")) if index == 0 else item
            for index, item in enumerate(BARS)
        )
        with pytest.raises(ValueError, match="checksum"):
            ReplayPipeline.from_run(
                connection,
                run_id,
                bars=tampered,
                bindings_factory=lambda: [
                    DetectorBinding(reversal_definition(), reversal_detector()),
                ],
                pattern_definitions=definitions(),
                calendar_resolver=calendar_resolver(),
            )
    engine.dispose()


def test_calendar_resolution_is_pinned_to_the_snapshot_identity() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    with engine.begin() as connection:
        prepare(connection)
        create_run(connection, run_id)
        with pytest.raises(ReplayPipelineError, match="calendar"):
            ReplayPipeline.from_run(
                connection,
                run_id,
                bars=BARS,
                bindings_factory=lambda: [
                    DetectorBinding(reversal_definition(), reversal_detector()),
                ],
                pattern_definitions=definitions(),
                calendar_resolver=lambda calendar_id, version: replace(calendar(), version="other"),
            )
    engine.dispose()


def test_only_the_application_layer_depends_on_detection() -> None:
    """SCRUM-61 wiring moved the runtime into application orchestration."""
    offenders: list[str] = []
    for path in Path("src/market_analysis").rglob("*.py"):
        if path.parent.name in {"detection", "application"}:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import) and any(
                "detection" in alias.name for alias in node.names
            ):
                offenders.append(str(path))
            elif isinstance(node, ast.ImportFrom) and (
                (node.module and "detection" in node.module)
                or any(
                    alias.name == "detection" or alias.name.startswith("detection.")
                    for alias in node.names
                )
            ):
                offenders.append(str(path))
    assert offenders == []


def test_start_requires_a_created_run_and_transition_is_recorded() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    with engine.begin() as connection:
        prepare(connection)
        create_run(connection, run_id)
        pipeline, _ = build_pipeline(connection, run_id)
        pipeline.start(connection, at=START)
        with pytest.raises(ReplayPipelineError, match="only a created replay run"):
            pipeline.start(connection, at=START)
        # PAUSED runs refuse to step; the lifecycle transition stays explicit
        transition_replay_run(
            connection, run_id, ReplayStatus.PAUSED, at=START + timedelta(seconds=1)
        )
        with pytest.raises(ReplayPipelineError, match="not running"):
            pipeline.step(connection)
        assert pipeline.cursor_index == -1
        assert pipeline.runtime.aggregator.completed_bars == 0
        transition_replay_run(connection, run_id, ReplayStatus.RUNNING, at=START)
        assert pipeline.step(connection).index == 0
        assert pipeline.run_to_completion(connection, at=SELECTED_END).record.status is (
            ReplayStatus.COMPLETED
        )
    engine.dispose()


def test_pipeline_matches_direct_runtime_and_retains_only_observable_prefixes() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    with engine.begin() as connection:
        prepare(connection)
        create_run(connection, run_id)
        pipeline = run_to_completion(connection, run_id)
        direct = DetectorRuntime(
            pipeline.detection_config,
            [DetectorBinding(reversal_definition(), reversal_detector())],
            run_id=str(run_id), dataset_revision_id=REVISION,
            calendar=calendar(), pinned_calendar_version=CALENDAR_VERSION,
            pinned_config_hash=pipeline.snapshot.detection_config_hash,
        )
        for index, step in enumerate(pipeline.steps):
            expected = direct.process_bar(BARS[index])
            assert step.result.frame.debug_json() == expected.frame.debug_json()
            assert step.result.events == expected.events
            assert step.view.history() == BARS[:index + 1]
            with pytest.raises(SimulationClockError, match="future"):
                step.view.bar_at(index + 1)
            with pytest.raises(SimulationClockError, match="future"):
                step.view.bar_at_time(step.view.timestamp + timedelta(minutes=1))
        assert direct.debug_json() == pipeline.runtime.debug_json()
    engine.dispose()


@pytest.mark.parametrize("mutation", [
    "runtime_reset", "aggregator_reset", "advance", "session_reset", "session_update",
    "aggregator_reset_replay",
])
def test_external_analytical_mutation_fails_without_advancing_cursor(mutation: str) -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    with engine.begin() as connection:
        prepare(connection)
        create_run(connection, run_id)
        pipeline, _ = build_pipeline(connection, run_id)
        pipeline.start(connection, at=START)
        pipeline.step(connection)
        if mutation == "runtime_reset":
            pipeline.runtime.reset()
        elif mutation == "aggregator_reset":
            pipeline.runtime.aggregator.reset()
        elif mutation == "aggregator_reset_replay":
            pipeline.runtime.aggregator.reset()
            pipeline.runtime.aggregator.update(BARS[0])
        elif mutation.startswith("session_"):
            session = pipeline.runtime.aggregator.session
            assert session is not None
            if mutation == "session_reset":
                session.reset()
            else:
                session.update(BARS[1])
        else:
            pipeline.runtime.process_bar(BARS[1])
        with pytest.raises(ReplayPipelineError, match="outside the pipeline"):
            pipeline.step(connection)
        lifecycle = load_replay_run(connection, run_id)
        assert lifecycle is not None
        assert lifecycle.status is ReplayStatus.FAILED
        assert lifecycle.cursor_index == pipeline.cursor_index == 0
        assert len(pipeline.steps) == 1
    engine.dispose()


@pytest.mark.parametrize("mutation", ["session_reset", "session_update"])
def test_component_mutation_after_final_bar_cannot_publish_completion(mutation: str) -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    with engine.begin() as connection:
        prepare(connection)
        create_run(connection, run_id)
        pipeline, _ = build_pipeline(connection, run_id)
        pipeline.start(connection, at=START)
        while pipeline.has_next:
            pipeline.step(connection)
        assert pipeline.cursor_index == len(BARS) - 1
        session = pipeline.runtime.aggregator.session
        assert session is not None
        if mutation == "session_reset":
            session.reset()
            assert session.state.completed_bars == 0
        else:
            session.update(bar(len(BARS)))
            assert session.state.completed_bars == len(BARS) + 1
        assert pipeline.runtime.aggregator.completed_bars == len(BARS)
        assert pipeline.runtime.processing_generation == len(BARS)
        with pytest.raises(ReplayPipelineError, match="outside the pipeline"):
            pipeline.run_to_completion(connection, at=SELECTED_END)
        lifecycle = load_replay_run(connection, run_id)
        assert lifecycle is not None
        assert lifecycle.status is ReplayStatus.FAILED
        assert lifecycle.cursor_index == len(BARS) - 1
        assert lifecycle.failure_reason == pipeline.failure_context
        with pytest.raises(ReplayPipelineError, match="latched"):
            pipeline.run_to_completion(connection, at=SELECTED_END)
    engine.dispose()


@pytest.mark.parametrize("failure", ["detector", "after_cursor_write"])
def test_failed_final_bar_cannot_complete_and_failure_survives_reload(
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    from market_analysis.application import replay_pipeline

    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    with engine.begin() as connection:
        prepare(connection)
        create_run(connection, run_id)
        detector = reversal_detector(
            fail_on_completed_bar=len(BARS) if failure == "detector" else None
        )
        pipeline, _ = build_pipeline(connection, run_id, detector)
        pipeline.start(connection, at=START)
        for _ in range(len(BARS) - 1):
            pipeline.step(connection)
        if failure == "after_cursor_write":
            original = replay_pipeline.advance_replay_cursor

            def write_then_fail(connection, run_id, index):
                original(connection, run_id, index)
                raise RuntimeError("injected failure after cursor write")

            monkeypatch.setattr(replay_pipeline, "advance_replay_cursor", write_then_fail)
        with pytest.raises((DetectorRuntimeError, RuntimeError), match="failure"):
            pipeline.step(connection)
        assert pipeline.has_next is False
        assert len(pipeline.steps) == len(BARS) - 1
        for operation in (
            lambda: pipeline.start(connection, at=START),
            lambda: pipeline.step(connection),
            lambda: pipeline.run_to_completion(connection, at=SELECTED_END),
        ):
            with pytest.raises(ReplayPipelineError, match="latched"):
                operation()
    # Catching inside engine.begin commits the separate FAILED transition;
    # a new connection proves this is persisted evidence, not log state.
    with engine.connect() as connection:
        context = load_replay_context(connection, run_id, pattern_definitions=definitions())
        assert context is not None
        assert context.lifecycle.status is ReplayStatus.FAILED
        assert context.lifecycle.cursor_index == len(BARS) - 2
        assert context.lifecycle.failure_reason == pipeline.failure_context
        assert context.lifecycle.completed_at is not None
        with pytest.raises(ReplayPipelineError, match="new run id"):
            build_pipeline(connection, run_id)
    engine.dispose()


def test_snapshot_tamper_and_missing_calendar_fail_closed() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    with engine.begin() as connection:
        prepare(connection)
        create_run(connection, run_id)
        with pytest.raises(ReplayPipelineError, match="calendar resolver"):
            ReplayPipeline.from_run(
                connection, run_id, bars=BARS,
                bindings_factory=lambda: [
                    DetectorBinding(reversal_definition(), reversal_detector()),
                ],
                pattern_definitions=definitions(),
            )
        connection.execute(update(run_snapshots).where(
            run_snapshots.c.run_id == str(run_id)
        ).values(detection_config_hash="0" * 64))
        with pytest.raises(ValueError, match="snapshot hash"):
            build_pipeline(connection, run_id)
    engine.dispose()


def test_pause_keeps_analytical_state_and_resume_processes_the_next_bar() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    with engine.begin() as connection:
        prepare(connection)
        create_run(connection, run_id)
        pipeline, detector = build_pipeline(connection, run_id)
        pipeline.start(connection, at=START)
        for _ in range(6):
            pipeline.step(connection)
        before = pipeline.runtime.debug_json()
        steps = pipeline.steps
        transition_replay_run(connection, run_id, ReplayStatus.PAUSED, at=START)
        for operation in (
            lambda: pipeline.step(connection),
            lambda: pipeline.run_to_completion(connection, at=SELECTED_END),
        ):
            with pytest.raises(ReplayPipelineError, match="not running"):
                operation()
        assert pipeline.steps == steps
        assert pipeline.runtime.debug_json() == before
        assert pipeline.cursor_index == 5
        assert pipeline.runtime.aggregator.completed_bars == len(detector.inputs) == 6
        transition_replay_run(connection, run_id, ReplayStatus.RUNNING, at=START)
        resumed = pipeline.step(connection)
        assert resumed.index == 6
        assert resumed.result.frame.completed_bars == len(detector.inputs) == 7
        assert resumed.view.history() == BARS[:7]
    engine.dispose()


def test_failure_status_write_error_does_not_replace_the_analytical_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_analysis.application import replay_pipeline

    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    with engine.begin() as connection:
        prepare(connection)
        create_run(connection, run_id)
        pipeline, _ = build_pipeline(connection, run_id, reversal_detector(fail_on_completed_bar=1))
        pipeline.start(connection, at=START)

        def unavailable(*args, **kwargs):
            raise RuntimeError("failure-status write unavailable")

        monkeypatch.setattr(replay_pipeline, "transition_replay_run", unavailable)
        with pytest.raises(DetectorRuntimeError, match="scripted detector failure") as caught:
            pipeline.step(connection)
        assert "FAILED status could not be persisted" in caught.value.__notes__[0]
        assert pipeline.failure_context is not None
        with pytest.raises(ReplayPipelineError, match="latched"):
            pipeline.run_to_completion(connection, at=SELECTED_END)
        lifecycle = load_replay_run(connection, run_id)
        assert lifecycle is not None and lifecycle.cursor_index == -1
        assert lifecycle.status is ReplayStatus.RUNNING  # explicit storage outage limit
    engine.dispose()


@pytest.mark.parametrize("selected_count", [13, 15])
@pytest.mark.parametrize("external_failure", [False, True])
def test_external_frame_processing_after_last_bar_prevents_completion(
    selected_count: int, external_failure: bool,
) -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    run_id = uuid4()
    end = START + timedelta(minutes=selected_count)
    with engine.begin() as connection:
        prepare(connection)
        create_run(connection, run_id, selected_end=end)
        detector = reversal_detector(
            fail_on_completed_bar=selected_count + 1 if external_failure else None
        )
        pipeline, _ = build_pipeline(connection, run_id, detector)
        pipeline.start(connection, at=START)
        for _ in range(selected_count):
            pipeline.step(connection)
        assert not pipeline.has_next
        external = MarketStateAggregator(
            pipeline.detection_config, run_id=str(run_id), dataset_revision_id=REVISION,
            calendar=calendar(), pinned_calendar_version=CALENDAR_VERSION,
            pinned_config_hash=pipeline.snapshot.detection_config_hash,
            pattern_definitions=definitions(),
        )
        # A genuine same-lineage frame from an independent aggregator is
        # outside the selected half-open interval. Bar 13 confirms an event;
        # bar 15 produces no detector events and cannot be caught by comparing
        # the debug event/instance payload alone.
        series = BARS[:selected_count + 1] if selected_count == 13 else BARS + (bar(15),)
        frame = None
        for item in series:
            frame = external.update(item)
        assert frame is not None and frame.bar.timestamp == end
        if external_failure:
            with pytest.raises(DetectorRuntimeError, match="scripted detector failure"):
                pipeline.runtime.process_frame(frame)
        else:
            result = pipeline.runtime.process_frame(frame)
            assert bool(result.events) is (selected_count == 13)
        assert len(detector.inputs) == selected_count + 1
        assert pipeline.runtime.aggregator.completed_bars == selected_count
        assert pipeline.runtime.processing_generation == selected_count + 1
        with pytest.raises(ReplayPipelineError, match="outside the pipeline"):
            pipeline.run_to_completion(connection, at=end)
        lifecycle = load_replay_run(connection, run_id)
        assert lifecycle is not None
        assert lifecycle.status is ReplayStatus.FAILED
        assert lifecycle.cursor_index == pipeline.cursor_index == selected_count - 1
        assert len(pipeline.steps) == selected_count
        with pytest.raises(ReplayPipelineError, match="latched"):
            pipeline.step(connection)
    engine.dispose()


def test_binding_factory_owns_fresh_state_for_repeat_and_failure_recovery() -> None:
    class Accumulator(ScriptedDetector):
        def __init__(self, *, fail_on_completed_bar: int | None) -> None:
            super().__init__(fail_on_completed_bar=fail_on_completed_bar)
            self.total = Decimal("999")  # execution must explicitly initialize via reset
            self.resets = 0

        def reset(self) -> None:
            super().reset()
            self.total = Decimal("0")
            self.resets += 1

        def process_bar(self, bar_input: DetectorInput) -> DetectorOutput:
            self.total += bar_input.frame.bar.close
            result = super().process_bar(bar_input)
            return replace(result, instance=replace(
                result.instance, context={**result.instance.context, "running_close": self.total},
            ))

    owned: list[Accumulator] = []
    failure_bar = None

    def factory() -> tuple[DetectorBinding, ...]:
        detector = Accumulator(fail_on_completed_bar=failure_bar)
        owned.append(detector)
        return (DetectorBinding(reversal_definition(), detector),)

    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        prepare(connection)

        def fresh() -> ReplayPipeline:
            run_id = uuid4()
            create_run(connection, run_id)
            return ReplayPipeline.from_run(
                connection, run_id, bars=BARS, bindings_factory=factory,
                pattern_definitions=definitions(), calendar_resolver=calendar_resolver(),
            )

        first = fresh()
        assert owned[0].total == 0 and owned[0].resets == 1
        first.start(connection, at=START)
        for _ in range(6):
            first.step(connection)
        partial_total = owned[0].total
        second = fresh()  # initializing a second pipeline cannot reset the active first
        assert owned[0].total == partial_total and owned[0].resets == 1
        assert owned[1] is not owned[0] and owned[1].total == 0 and owned[1].resets == 1
        first.run_to_completion(connection, at=SELECTED_END)
        second.run_to_completion(connection, at=SELECTED_END)
        assert first.runtime.instances == second.runtime.instances
        assert owned[0].total == owned[1].total == sum(item.close for item in BARS)
        failure_bar = 9
        failed = fresh()
        with pytest.raises(DetectorRuntimeError, match="scripted detector failure"):
            failed.run_to_completion(connection, at=SELECTED_END)
        failure_bar = None
        recovered = fresh()
        recovered.run_to_completion(connection, at=SELECTED_END)
        assert recovered.runtime.instances == first.runtime.instances
        assert len(owned) == 4 and len({id(detector) for detector in owned}) == 4
        assert all(detector.resets == 1 for detector in owned)
    engine.dispose()
