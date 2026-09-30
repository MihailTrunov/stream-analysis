"""Contract tests for the common deterministic detector runtime (SCRUM-80).

Fixtures are built only from market-state primitives — canonical bars, the
SCRUM-77 aggregator and SCRUM-78 pattern definitions — never from detector
internals, so every fake detector exercises the same generic runtime path.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from market_analysis.config import (
    ComponentSelection,
    ConfigParameter,
    DetectionAnalysisConfig,
    PatternSelection,
    detection_config_hash,
)
from market_analysis.detection import (
    DetectorBinding,
    DetectorEvent,
    DetectorInput,
    DetectorOutput,
    DetectorRecordError,
    DetectorRuntime,
    DetectorRuntimeError,
    DetectorRuntimeResult,
    PatternInstance,
    TransitionIntent,
)
from market_analysis.domain import Bar, Timeframe
from market_analysis.indicators import (
    MarketEvent,
    MarketEventType,
    MarketStateAggregator,
    StructureBreak,
    market_event_semantic_ref,
)
from market_analysis.patterns import (
    ConditionGroup,
    ContextFieldSpec,
    ParameterSpec,
    ParameterType,
    PatternDefinition,
    PatternDefinitionRegistry,
    TransitionSpec,
)

START = datetime(2026, 1, 5, 12, tzinfo=UTC)

# Hand-reviewed real-chain series (UP direction), identical to the SCRUM-77
# fixture: EMA crosses on bars 5/6/8/11/12/14, swing confirmations and
# classifications on bars 6/8/10/12/14, the protected-high close break on bar
# 13, and the qualified leg start on bar 14 (its event_time cites bar 11).
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
        "US30",
        Timeframe.M1,
        START + timedelta(minutes=index),
        Decimal(close),
        Decimal(high),
        Decimal(low),
        Decimal(close),
    )


def chain_series() -> tuple[Bar, ...]:
    return tuple(bar(i, "100", "101", "99") for i in range(5)) + tuple(
        bar(i, close, high, low) for i, (close, high, low) in enumerate(TRIPLES, 5)
    )


def component_selections() -> tuple[ComponentSelection, ...]:
    return (
        ComponentSelection(
            component_id="atr",
            component_version="1",
            parameters=(ConfigParameter(name="period", value=1),),
        ),
        ComponentSelection(
            component_id="ema",
            component_version="1",
            parameters=(ConfigParameter(name="period", value=3),),
        ),
        ComponentSelection(
            component_id="swing_point",
            component_version="1",
            parameters=(
                ConfigParameter(name="atr_period", value=1),
                ConfigParameter(name="reversal_atr_multiplier", value=Decimal("0.1")),
            ),
        ),
        ComponentSelection(component_id="swing_structure", component_version="1"),
        ComponentSelection(component_id="trend_leg", component_version="1"),
        ComponentSelection(
            component_id="trend_leg_qualification",
            component_version="1",
            parameters=(
                ConfigParameter(name="min_duration_bars", value=4),
                ConfigParameter(name="min_directional_move_points", value=Decimal("31")),
            ),
        ),
        ComponentSelection(component_id="range_state", component_version="1"),
    )


def config_with(patterns: list[PatternSelection]) -> DetectionAnalysisConfig:
    return DetectionAnalysisConfig(
        instrument_id="US30",
        timeframe=Timeframe.M1,
        calendar_id="cal-v1",
        components=component_selections(),
        patterns=tuple(patterns),
    )


def pattern_selection(definition: PatternDefinition) -> PatternSelection:
    return PatternSelection(
        pattern_id=definition.pattern_id,
        pattern_version=definition.pattern_version,
    )


def reversal_definition(version: str = "1") -> PatternDefinition:
    """SCRUM-83 stand-in: an EMA cross opens a candidate, a close break confirms."""
    return PatternDefinition(
        "trend-reversal",
        version,
        "Trend reversal",
        "Reversal hypothesis",
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


def continuation_definition(
    version: str = "1", confirm: str = "continuation_break"
) -> PatternDefinition:
    """SCRUM-85 stand-in: reclaim and break may land on one completed bar."""
    return PatternDefinition(
        "trend-continuation",
        version,
        "Trend continuation",
        "Continuation hypothesis",
        ("trend_leg", "ema"),
        (),
        (ParameterSpec("expiry_bars", ParameterType.INTEGER, 60, minimum=Decimal("1")),),
        ("idle", "candidate", "reclaimed", "confirmed"),
        (
            TransitionSpec("idle", "candidate", "opposing_ema_cross"),
            TransitionSpec("candidate", "reclaimed", "ema_reclaim"),
            TransitionSpec("reclaimed", "confirmed", confirm),
        ),
        (
            ConditionGroup("candidate", ("opposing_ema_cross",)),
            ConditionGroup("qualification", ("ema_reclaim", confirm)),
        ),
        ("ema_reclaim", confirm),
        (
            ContextFieldSpec("frozen_directional_swing", "event_ref"),
            ContextFieldSpec("frozen_protected_swing", "event_ref"),
        ),
        ("opposing_ema_cross", "ema_reclaim", confirm),
        same_bar_chains=(("ema_reclaim", confirm),),
    )


def compression_definition() -> PatternDefinition:
    """SCRUM-84 stand-in: compression entry, persistence and release states."""
    return PatternDefinition(
        "range-compression",
        "1",
        "Range compression",
        "Compression hypothesis",
        ("range_state",),
        (),
        (ParameterSpec("persistence_bars", ParameterType.INTEGER, 3, minimum=Decimal("1")),),
        ("idle", "candidate", "active", "completed"),
        (
            TransitionSpec("idle", "candidate", "compression_entry_pass"),
            TransitionSpec("candidate", "active", "persistence_met"),
            TransitionSpec("active", "completed", "compression_released"),
        ),
        (
            ConditionGroup("formation", ("compression_entry_pass", "persistence_met")),
            ConditionGroup("completion", ("compression_released",)),
        ),
        ("compression_entry_pass", "persistence_met", "compression_released"),
        (ContextFieldSpec("qualifying_count", "int"),),
        ("compression_entry_pass", "persistence_met", "compression_released"),
    )


class ScriptedDetector:
    """Fake detector standing in for a SCRUM-83/84/85 implementation.

    Reacts only to canonical MarketEvent payloads carried by the frame feed,
    mapping each observed event type to an ordered trigger chain of its own
    definition. Inputs and consumed events are recorded so tests can assert
    frame identity and payload identity without recomputation.
    """

    def __init__(
        self,
        *,
        reactions: dict[MarketEventType, tuple[str, ...]] | None = None,
        fail_on_completed_bar: int | None = None,
    ) -> None:
        self._reactions = dict(reactions or {})
        self._fail_on_completed_bar = fail_on_completed_bar
        self.inputs: list[DetectorInput] = []
        self.consumed: list[MarketEvent] = []

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
            self.consumed.append(event)
            for trigger, from_state, to_state in chain:
                context[f"{trigger}:ordinal"] = event.ordinal
                end_state = to_state
                intents.append(
                    TransitionIntent(
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
                    )
                )
            break  # one transition set per bar; same-bar chains ride one event
        return DetectorOutput(
            instance=replace(bar_input.instance, state=end_state, context=context),
            transitions=tuple(intents),
        )

    def reset(self) -> None:
        self.inputs.clear()
        self.consumed.clear()


class RogueIntentDetector:
    """Emit one hand-built intent on one bar to exercise runtime rejection paths."""

    def __init__(self, *, on_completed_bar: int, transitions: tuple[TransitionIntent, ...]) -> None:
        self._on_completed_bar = on_completed_bar
        self._transitions = transitions

    def process_bar(self, bar_input: DetectorInput) -> DetectorOutput:
        if bar_input.completed_bars == self._on_completed_bar:
            end_state = (
                self._transitions[-1].to_state if self._transitions else bar_input.instance.state
            )
            return DetectorOutput(
                instance=replace(bar_input.instance, state=end_state),
                transitions=self._transitions,
            )
        return DetectorOutput(instance=bar_input.instance)

    def reset(self) -> None:
        return None


def _chain(
    definition: PatternDefinition,
    state: str,
    triggers: tuple[str, ...],
) -> tuple[tuple[str, str, str], ...] | None:
    chain: list[tuple[str, str, str]] = []
    current = state
    for trigger in triggers:
        edge = next(
            (
                item
                for item in definition.transitions
                if item.from_state == current and item.trigger_id == trigger
            ),
            None,
        )
        if edge is None:
            return None
        chain.append((trigger, edge.from_state, edge.to_state))
        current = edge.to_state
    return tuple(chain)


def reversal_detector(**kwargs: Any) -> ScriptedDetector:
    return ScriptedDetector(
        reactions={
            MarketEventType.EMA_CROSS: ("opposing_ema_cross",),
            MarketEventType.SWING_HIGH_CLOSE_BREAK: ("protected_swing_break",),
            MarketEventType.SWING_LOW_CLOSE_BREAK: ("protected_swing_break",),
        },
        **kwargs,
    )


def continuation_detector(confirm: str = "continuation_break") -> ScriptedDetector:
    return ScriptedDetector(
        reactions={
            MarketEventType.EMA_CROSS: ("opposing_ema_cross",),
            MarketEventType.SWING_HIGH_CLOSE_BREAK: ("ema_reclaim", confirm),
            MarketEventType.SWING_LOW_CLOSE_BREAK: ("ema_reclaim", confirm),
        }
    )


def compression_detector() -> ScriptedDetector:
    return ScriptedDetector(
        reactions={
            MarketEventType.SWING_STRUCTURE_CLASSIFIED: ("compression_entry_pass",),
            MarketEventType.EMA_CROSS: ("persistence_met",),
            MarketEventType.TREND_LEG_STARTED: ("compression_released",),
        }
    )


def runtime_for(
    bindings: list[DetectorBinding],
    definitions: list[PatternDefinition],
) -> DetectorRuntime:
    return DetectorRuntime(
        config_with([pattern_selection(item) for item in definitions]),
        bindings,
        run_id="run-80",
        dataset_revision_id="dataset-80",
    )


def three_detector_runtime(
    **reversal_kwargs: Any,
) -> tuple[DetectorRuntime, tuple[ScriptedDetector, ScriptedDetector, ScriptedDetector]]:
    """Reversal, continuation and compression stand-ins through one runtime."""
    reversal = reversal_definition()
    continuation = continuation_definition()
    compression = compression_definition()
    detectors = (
        reversal_detector(**reversal_kwargs),
        continuation_detector(),
        compression_detector(),
    )
    bindings = [
        DetectorBinding(compression, detectors[2]),  # scrambled on purpose
        DetectorBinding(reversal, detectors[0]),
        DetectorBinding(continuation, detectors[1]),
    ]
    return runtime_for(bindings, [reversal, continuation, compression]), detectors


def drive(
    runtime: DetectorRuntime,
    series: tuple[Bar, ...],
) -> list[DetectorRuntimeResult]:
    return [runtime.process_bar(item) for item in series]


def _transitions(
    runtime: DetectorRuntime,
    pattern_id: str,
) -> list[tuple[int, str, str, str]]:
    return [
        (event.sequence, event.from_state, event.to_state, event.trigger_id)
        for event in runtime.events
        if event.pattern_id == pattern_id
    ]


def test_detector_input_contract_is_frozen_and_frame_scoped() -> None:
    definition = continuation_definition()
    instance = PatternInstance(
        definition.pattern_id,
        definition.pattern_version,
        definition.pattern_id,
        definition.lifecycle_states[0],
    )
    definitions = {definition.identity: definition}
    aggregator = MarketStateAggregator(
        config_with([pattern_selection(definition)]),
        run_id="run-80",
        dataset_revision_id="dataset-80",
        pattern_definitions=definitions,
    )
    frame = aggregator.update(bar(0))
    bar_input = DetectorInput(
        frame=frame,
        definition=definition,
        parameters=definition.resolve_parameters(),
        config=config_with([pattern_selection(definition)]),
        detection_config_hash=frame.detection_config_hash,
        run_id="run-80",
        dataset_revision_id="dataset-80",
        instance=instance,
    )
    assert set(DetectorInput.__dataclass_fields__) == {
        "frame",
        "definition",
        "parameters",
        "config",
        "detection_config_hash",
        "run_id",
        "dataset_revision_id",
        "instance",
    }
    assert bar_input.market_events is frame.market_events_this_bar
    assert bar_input.bar is frame.bar
    assert bar_input.bar_timestamp == bar_input.detection_time == bar(0).timestamp
    assert bar_input.instrument_id == "US30"
    assert bar_input.timeframe is Timeframe.M1
    assert bar_input.pattern_id == "trend-continuation"
    assert bar_input.pattern_version == "1"
    assert bar_input.parameters["expiry_bars"] == 60
    with pytest.raises(FrozenInstanceError):
        bar_input.run_id = "other"  # type: ignore[misc]
    with pytest.raises(TypeError, match="does not support item assignment"):
        bar_input.parameters["expiry_bars"] = 1  # type: ignore[index]
    with pytest.raises(DetectorRecordError, match="config hash"):
        replace(bar_input, detection_config_hash="deadbeef")
    with pytest.raises(DetectorRecordError, match="run identity"):
        replace(bar_input, run_id="other-run")
    with pytest.raises(DetectorRecordError, match="instance identity"):
        replace(bar_input, instance=replace(instance, pattern_id="other-pattern"))
    with pytest.raises(DetectorRecordError, match="lifecycle state"):
        replace(bar_input, instance=replace(instance, state="undeclared"))


def test_pattern_selections_hash_into_the_canonical_frame() -> None:
    definition = continuation_definition()
    definitions = {definition.identity: definition}
    config = config_with([pattern_selection(definition)])
    aggregator = MarketStateAggregator(
        config,
        run_id="run-80",
        dataset_revision_id="dataset-80",
        pattern_definitions=definitions,
    )
    frame = aggregator.update(bar(0))
    assert frame.detection_config_hash == detection_config_hash(
        config, pattern_definitions=definitions
    )
    plain = DetectionAnalysisConfig(
        instrument_id="US30",
        timeframe=Timeframe.M1,
        calendar_id="cal-v1",
        components=component_selections(),
    )
    assert frame.detection_config_hash != detection_config_hash(plain)


def test_runtime_construction_contracts() -> None:
    reversal = reversal_definition()
    continuation = continuation_definition()
    detectors = (reversal_detector(), continuation_detector())
    with pytest.raises(DetectorRuntimeError, match="has no detector binding"):
        runtime_for([DetectorBinding(continuation, detectors[1])], [reversal, continuation])
    with pytest.raises(DetectorRuntimeError, match="no enabled pattern selection"):
        runtime_for(
            [DetectorBinding(reversal, detectors[0]), DetectorBinding(continuation, detectors[1])],
            [reversal],
        )
    with pytest.raises(DetectorRuntimeError, match="duplicate detector binding"):
        runtime_for(
            [DetectorBinding(reversal, detectors[0]), DetectorBinding(reversal, detectors[1])],
            [reversal],
        )
    stray = config_with([PatternSelection(pattern_id="trend-reversal", pattern_version="9")])
    with pytest.raises(DetectorRuntimeError, match="invalid detector runtime config"):
        DetectorRuntime(
            stray,
            [DetectorBinding(reversal, detectors[0])],
            run_id="run-80",
            dataset_revision_id="dataset-80",
        )

    class NotADetector:
        pass

    with pytest.raises(DetectorRuntimeError, match="PatternDetector protocol"):
        DetectorRuntime(
            config_with([pattern_selection(reversal)]),
            [DetectorBinding(reversal, NotADetector())],  # type: ignore[arg-type]
            run_id="run-80",
            dataset_revision_id="dataset-80",
        )
    runtime, _ = three_detector_runtime()
    with pytest.raises(DetectorRuntimeError, match="canonical Bar"):
        runtime.process_bar("not-a-bar")  # type: ignore[arg-type]


def test_two_detectors_execute_through_one_interface() -> None:
    reversal = reversal_definition()
    continuation = continuation_definition()
    detectors = (reversal_detector(), continuation_detector())
    runtime = runtime_for(
        [DetectorBinding(reversal, detectors[0]), DetectorBinding(continuation, detectors[1])],
        [reversal, continuation],
    )
    results = drive(runtime, chain_series())
    assert all(isinstance(result, DetectorRuntimeResult) for result in results)
    for first, second in zip(detectors[0].inputs, detectors[1].inputs, strict=True):
        assert isinstance(first, DetectorInput) and isinstance(second, DetectorInput)
        assert first.frame is second.frame
    assert {event.pattern_id for event in runtime.events} == {
        "trend-reversal",
        "trend-continuation",
    }
    assert _transitions(runtime, "trend-reversal") == [
        (0, "idle", "candidate", "opposing_ema_cross"),
        (1, "candidate", "confirmed", "protected_swing_break"),
    ]
    assert _transitions(runtime, "trend-continuation") == [
        (0, "idle", "candidate", "opposing_ema_cross"),
        (1, "candidate", "reclaimed", "ema_reclaim"),
        (2, "reclaimed", "confirmed", "continuation_break"),
    ]


def test_representative_scrum_83_84_85_detectors_share_one_path() -> None:
    runtime, detectors = three_detector_runtime()
    drive(runtime, chain_series())
    assert _transitions(runtime, "trend-reversal") == [
        (0, "idle", "candidate", "opposing_ema_cross"),
        (1, "candidate", "confirmed", "protected_swing_break"),
    ]
    assert _transitions(runtime, "trend-continuation") == [
        (0, "idle", "candidate", "opposing_ema_cross"),
        (1, "candidate", "reclaimed", "ema_reclaim"),
        (2, "reclaimed", "confirmed", "continuation_break"),
    ]
    assert _transitions(runtime, "range-compression") == [
        (0, "idle", "candidate", "compression_entry_pass"),
        (1, "candidate", "active", "persistence_met"),
        (2, "active", "completed", "compression_released"),
    ]
    assert [instance.state for instance in runtime.instances] == [
        "completed",
        "confirmed",
        "confirmed",
    ]
    # completion cites bar 11's extreme, whose TREND_LEG_STARTED event is
    # visible on the completion frame itself — an earlier extreme, no look-ahead
    released = runtime.events[-1]
    assert released.pattern_id == "range-compression"
    assert released.event_time == START + timedelta(minutes=11)
    assert released.detection_time == START + timedelta(minutes=14)
    assert detectors[0].inputs[14].parameters["expiry_bars"] == 60
    assert detectors[1].inputs[14].parameters["expiry_bars"] == 60
    assert detectors[2].inputs[14].parameters["persistence_bars"] == 3


def test_same_bar_multi_transition_is_preserved_in_order() -> None:
    runtime, _ = three_detector_runtime()
    series = chain_series()
    drive(runtime, series[:13])
    result = runtime.process_bar(series[13])
    chain = [event for event in result.events if event.pattern_id == "trend-continuation"]
    assert [
        (event.sequence, event.from_state, event.to_state, event.trigger_id) for event in chain
    ] == [
        (1, "candidate", "reclaimed", "ema_reclaim"),
        (2, "reclaimed", "confirmed", "continuation_break"),
    ]
    assert len(chain) == 2  # both transitions survive; never one opaque state jump
    assert chain[0].detection_time == chain[1].detection_time == series[13].timestamp
    assert runtime.instances[1].state == "confirmed"


def test_detector_order_is_canonical_and_hash_consistent() -> None:
    runtime, _ = three_detector_runtime()
    assert [
        (binding.definition.pattern_id, binding.definition.pattern_version)
        for binding in runtime.bindings
    ] == [
        ("range-compression", "1"),
        ("trend-continuation", "1"),
        ("trend-reversal", "1"),
    ]
    results = drive(runtime, chain_series())
    assert [(event.pattern_id, event.trigger_id) for event in results[13].events] == [
        ("trend-continuation", "ema_reclaim"),
        ("trend-continuation", "continuation_break"),
        ("trend-reversal", "protected_swing_break"),
    ]


def test_pattern_tuple_order_never_changes_detector_order_events_or_hash() -> None:
    reversal = reversal_definition()
    continuation = continuation_definition()
    forward = DetectorRuntime(
        config_with([pattern_selection(reversal), pattern_selection(continuation)]),
        [
            DetectorBinding(reversal, reversal_detector()),
            DetectorBinding(continuation, continuation_detector()),
        ],
        run_id="run-80",
        dataset_revision_id="dataset-80",
    )
    backward = DetectorRuntime(
        config_with([pattern_selection(continuation), pattern_selection(reversal)]),
        [
            DetectorBinding(continuation, continuation_detector()),
            DetectorBinding(reversal, reversal_detector()),
        ],
        run_id="run-80",
        dataset_revision_id="dataset-80",
    )
    assert forward.detection_config_hash == backward.detection_config_hash
    assert forward.binding_fingerprint == backward.binding_fingerprint
    assert [binding.definition.pattern_id for binding in forward.bindings] == [
        "trend-continuation",
        "trend-reversal",
    ]
    assert [binding.definition.pattern_id for binding in backward.bindings] == [
        "trend-continuation",
        "trend-reversal",
    ]
    series = chain_series()
    drive(forward, series)
    drive(backward, series)
    assert forward.debug_json() == backward.debug_json()
    assert [event.to_canonical_dict() for event in forward.events] == [
        event.to_canonical_dict() for event in backward.events
    ]


def test_failure_latches_the_runtime_until_reset() -> None:
    series = chain_series()
    runtime, _ = three_detector_runtime(fail_on_completed_bar=14)
    drive(runtime, series[:13])
    committed = runtime.events
    baseline = runtime.debug_json()
    with pytest.raises(DetectorRuntimeError, match="scripted detector failure"):
        runtime.process_bar(series[13])
    with pytest.raises(DetectorRuntimeError, match="latched"):
        runtime.process_bar(series[14])
    assert runtime.debug_json() == baseline
    frame_runtime, _ = three_detector_runtime(fail_on_completed_bar=14)
    drive(frame_runtime, series[:13])
    frame = frame_runtime.aggregator.update(series[13])
    with pytest.raises(DetectorRuntimeError, match="scripted detector failure"):
        frame_runtime.process_frame(frame)
    with pytest.raises(DetectorRuntimeError, match="latched"):
        frame_runtime.process_frame(frame)
    runtime.reset()
    drive(runtime, series[:13])
    assert runtime.debug_json() == baseline
    assert runtime.events == committed


def test_frames_are_shared_and_detectors_cannot_leak_within_a_bar() -> None:
    runtime, detectors = three_detector_runtime()
    reversal, continuation, compression = detectors
    results = drive(runtime, chain_series())
    for index in range(15):
        assert len({id(detector.inputs[index].frame) for detector in detectors}) == 1
        assert reversal.inputs[index].frame is results[index].frame
    # the earlier detector emits on bar 13; the later detectors still see the
    # same frame object and their own pre-bar instance state
    assert continuation.inputs[13].frame is reversal.inputs[13].frame
    assert continuation.inputs[13].instance.state == "candidate"
    assert continuation.inputs[13].instance.pattern_id == "trend-continuation"
    assert compression.inputs[13].instance.state == "active"


def test_canonical_break_events_reach_detectors_by_identity() -> None:
    runtime, detectors = three_detector_runtime()
    reversal = detectors[0]
    frames = [result.frame for result in drive(runtime, chain_series())]
    (break_event,) = [
        event
        for event in frames[13].market_events_this_bar
        if event.event_type is MarketEventType.SWING_HIGH_CLOSE_BREAK
    ]
    evidence = break_event.evidence
    assert isinstance(evidence, StructureBreak)
    assert evidence.breaking_close == Decimal(140)
    consumed = [
        event
        for event in reversal.consumed
        if event.event_type is MarketEventType.SWING_HIGH_CLOSE_BREAK
    ]
    assert len(consumed) == 1
    assert consumed[0] is break_event
    assert consumed[0].evidence is evidence
    assert reversal.inputs[13].market_events == frames[13].market_events_this_bar


def test_committed_events_carry_full_runtime_context() -> None:
    runtime, detectors = three_detector_runtime()
    drive(runtime, chain_series())
    assert len(runtime.events) == 8
    for event in runtime.events:
        assert isinstance(event, DetectorEvent)
        assert event.run_id == "run-80"
        assert event.dataset_revision_id == "dataset-80"
        assert event.instrument_id == "US30"
        assert event.timeframe == "1m"
        assert event.detection_config_hash == runtime.detection_config_hash
        assert event.pattern_id and event.pattern_version and event.instance_id
        assert event.rationale["condition"] == event.trigger_id
        assert event.rationale["source_event"]
    for detector in detectors:
        first = detector.inputs[0]
        assert first.config is runtime.config
        assert first.detection_config_hash == runtime.detection_config_hash
        assert first.run_id == "run-80"
        assert first.dataset_revision_id == "dataset-80"


def test_strict_event_rationale_cites_only_observed_market_events() -> None:
    definition = replace(continuation_definition(), rationale_schema_version="detector-evidence-v1")

    class ReferencingDetector:
        def __init__(self, *, invented: bool) -> None:
            self.invented = invented

        def reset(self) -> None:
            pass

        def process_bar(self, bar_input: DetectorInput) -> DetectorOutput:
            if bar_input.instance.state != "idle" or not bar_input.market_events:
                return DetectorOutput(bar_input.instance)
            event = bar_input.market_events[0]
            reference = (
                "f" * 64 if self.invented else market_event_semantic_ref(bar_input.frame, event)
            )
            trigger = "opposing_ema_cross"
            return DetectorOutput(
                replace(bar_input.instance, state="candidate"),
                (
                    TransitionIntent(
                        bar_input.pattern_id,
                        bar_input.pattern_version,
                        bar_input.instance.instance_id,
                        "idle",
                        "candidate",
                        trigger,
                        event.event_time,
                        bar_input.detection_time,
                        {
                            "schema": "detector-evidence-v1",
                            "condition": trigger,
                            "source_market_event_refs": (reference,),
                            "items": (
                                {
                                    "condition_id": trigger,
                                    "status": "PASS",
                                    "value": {"type": "event_ref", "value": reference},
                                    "operator": "PRESENT",
                                    "threshold": None,
                                    "units": None,
                                    "source_refs": (reference,),
                                    "features": {},
                                },
                            ),
                        },
                    ),
                ),
            )

    valid = DetectorRuntime(
        config_with([pattern_selection(definition)]),
        [DetectorBinding(definition, ReferencingDetector(invented=False))],
        run_id="run-80",
        dataset_revision_id="dataset-80",
    )
    drive(valid, chain_series())
    assert valid.events[0].rationale["source_market_event_refs"]

    invalid = DetectorRuntime(
        config_with([pattern_selection(definition)]),
        [DetectorBinding(definition, ReferencingDetector(invented=True))],
        run_id="run-80",
        dataset_revision_id="dataset-80",
    )
    with pytest.raises(DetectorRuntimeError, match="not observed by this bar"):
        drive(invalid, chain_series())
    assert invalid.events == ()


def test_reset_replay_is_byte_identical_across_runs() -> None:
    runtime, _ = three_detector_runtime()
    series = chain_series()
    drive(runtime, series)
    baseline_events = runtime.events
    baseline = runtime.debug_json()
    runtime.reset()
    assert runtime.reset_generation == 2
    assert runtime.events == ()
    drive(runtime, series)
    assert runtime.events == baseline_events
    assert runtime.debug_json() == baseline
    fresh, _ = three_detector_runtime()
    drive(fresh, series)
    assert fresh.debug_json() == baseline
    assert [event.to_canonical_dict() for event in fresh.events] == [
        event.to_canonical_dict() for event in baseline_events
    ]


def test_failure_rolls_back_the_whole_bar_and_names_the_context() -> None:
    runtime, _ = three_detector_runtime(fail_on_completed_bar=14)
    series = chain_series()
    drive(runtime, series[:13])
    baseline = runtime.debug_json()
    committed = runtime.events
    with pytest.raises(DetectorRuntimeError) as excinfo:
        runtime.process_bar(series[13])
    message = str(excinfo.value)
    assert "run_id='run-80'" in message
    assert "dataset_revision_id='dataset-80'" in message
    assert "pattern=trend-reversal@1" in message
    assert "instance='trend-reversal'" in message
    assert "2026-01-05T12:13:00+00:00" in message
    assert "scripted detector failure" in message
    assert runtime.debug_json() == baseline  # state equals the pre-bar state exactly
    assert runtime.events == committed
    runtime.reset()
    drive(runtime, series[:13])
    assert runtime.debug_json() == baseline  # documented recovery: reset + replay


def test_illegal_transitions_are_rejected_with_context() -> None:
    definition = continuation_definition()
    series = chain_series()
    undeclared = TransitionIntent(
        pattern_id="trend-continuation",
        pattern_version="1",
        instance_id="trend-continuation",
        from_state="candidate",
        to_state="confirmed",
        trigger_id="ema_reclaim",
        event_time=START + timedelta(minutes=5),
        detection_time=START + timedelta(minutes=6),
        rationale={"condition": "ema_reclaim"},
    )
    wrong_source = TransitionIntent(
        pattern_id="trend-continuation",
        pattern_version="1",
        instance_id="trend-continuation",
        from_state="reclaimed",
        to_state="confirmed",
        trigger_id="continuation_break",
        event_time=START + timedelta(minutes=5),
        detection_time=START + timedelta(minutes=6),
        rationale={"condition": "continuation_break"},
    )
    for rogue_intent, detail in (
        (undeclared, "candidate->confirmed via 'ema_reclaim' is not declared"),
        (wrong_source, "do not match the lifecycle-derived sequence"),
    ):
        rogue = RogueIntentDetector(on_completed_bar=7, transitions=(rogue_intent,))
        runtime = DetectorRuntime(
            config_with([pattern_selection(definition)]),
            [DetectorBinding(definition, rogue)],
            run_id="run-80",
            dataset_revision_id="dataset-80",
        )
        drive(runtime, series[:6])
        baseline = runtime.debug_json()
        with pytest.raises(DetectorRuntimeError) as excinfo:
            runtime.process_bar(series[6])
        message = str(excinfo.value)
        assert "pattern=trend-continuation@1" in message
        assert "instance='trend-continuation'" in message
        assert "2026-01-05T12:06:00+00:00" in message
        assert detail in message
        assert runtime.debug_json() == baseline  # state un-advanced
        assert runtime.events == ()


def test_causality_guard_rejects_unseen_event_times() -> None:
    definition = continuation_definition()
    series = chain_series()
    current_cross = START + timedelta(minutes=5)

    def citing(event_time: datetime) -> TransitionIntent:
        return TransitionIntent(
            pattern_id="trend-continuation",
            pattern_version="1",
            instance_id="trend-continuation",
            from_state="idle",
            to_state="candidate",
            trigger_id="opposing_ema_cross",
            event_time=event_time,
            detection_time=current_cross,
            rationale={"condition": "opposing_ema_cross"},
        )

    rejecting = [
        citing(START + timedelta(minutes=13)),  # the future break; not observable yet
        citing(START + timedelta(minutes=3)),  # past bar, but no market event ever carried it
        replace(citing(current_cross), detection_time=START + timedelta(minutes=13)),
    ]
    for rogue_intent in rejecting:
        rogue = RogueIntentDetector(on_completed_bar=6, transitions=(rogue_intent,))
        runtime = DetectorRuntime(
            config_with([pattern_selection(definition)]),
            [DetectorBinding(definition, rogue)],
            run_id="run-80",
            dataset_revision_id="dataset-80",
        )
        drive(runtime, series[:5])
        baseline = runtime.debug_json()
        with pytest.raises(DetectorRuntimeError) as excinfo:
            runtime.process_bar(series[5])
        assert str(excinfo.value).startswith("run_id='run-80'")
        assert runtime.debug_json() == baseline
    rogue = RogueIntentDetector(on_completed_bar=6, transitions=(citing(current_cross),))
    runtime = DetectorRuntime(
        config_with([pattern_selection(definition)]),
        [DetectorBinding(definition, rogue)],
        run_id="run-80",
        dataset_revision_id="dataset-80",
    )
    drive(runtime, series[:6])
    assert runtime.instances[0].state == "candidate"
    assert runtime.events[0].event_time == current_cross


def test_two_registered_versions_execute_over_one_frozen_fixture() -> None:
    first = continuation_definition("1")
    second = continuation_definition("2", confirm="close_break_confirmed")
    registry = PatternDefinitionRegistry()
    registry.register(first)
    registry.register(second)
    assert registry.get("trend-continuation", "2") is second
    series = chain_series()  # one frozen fixture for both versions
    detectors = (continuation_detector(), continuation_detector("close_break_confirmed"))
    bindings = [
        DetectorBinding(second, detectors[1], instance_id="continuation-v2"),  # scrambled
        DetectorBinding(first, detectors[0], instance_id="continuation-v1"),
    ]
    runtime = DetectorRuntime(
        config_with([PatternSelection(pattern_id="trend-continuation", pattern_version="1")]),
        bindings,
        run_id="run-80",
        dataset_revision_id="dataset-80",
    )
    assert [binding.effective_instance_id for binding in runtime.bindings] == [
        "continuation-v1",
        "continuation-v2",
    ]
    drive(runtime, series)
    assert [
        (event.pattern_version, event.instance_id, event.sequence, event.to_state)
        for event in runtime.events
    ] == [
        ("1", "continuation-v1", 0, "candidate"),
        ("2", "continuation-v2", 0, "candidate"),
        ("1", "continuation-v1", 1, "reclaimed"),
        ("1", "continuation-v1", 2, "confirmed"),
        ("2", "continuation-v2", 1, "reclaimed"),
        ("2", "continuation-v2", 2, "confirmed"),
    ]
    assert [instance.state for instance in runtime.instances] == ["confirmed", "confirmed"]
    assert [instance.pattern_version for instance in runtime.instances] == ["1", "2"]
    baseline = runtime.debug_json()
    runtime.reset()
    drive(runtime, series)
    assert runtime.debug_json() == baseline


def test_frame_level_entry_point_and_lineage_guards() -> None:
    runtime, _ = three_detector_runtime()
    series = chain_series()
    frames = [runtime.aggregator.update(item) for item in series[:6]]
    for frame in frames:
        result = runtime.process_frame(frame)
        assert result.frame is frame
    assert {instance.pattern_id: instance.state for instance in runtime.instances} == {
        "range-compression": "idle",
        "trend-continuation": "candidate",
        "trend-reversal": "candidate",
    }
    with pytest.raises(DetectorRuntimeError, match="strictly increasing"):
        runtime.process_frame(frames[5])
    definitions = {binding.definition.identity: binding.definition for binding in runtime.bindings}
    other = MarketStateAggregator(
        runtime.config,
        run_id="other-run",
        dataset_revision_id="dataset-80",
        pattern_definitions=definitions,
    )
    with pytest.raises(DetectorRuntimeError, match="run_id"):
        runtime.process_frame(other.update(bar(6)))


@pytest.mark.parametrize("entry_point", ["process_bar", "process_frame"])
def test_processing_generation_counts_failed_attempts_and_survives_reset(entry_point: str) -> None:
    runtime, _ = three_detector_runtime(fail_on_completed_bar=2)
    assert runtime.processing_generation == 0
    method = getattr(runtime, entry_point)
    with pytest.raises(DetectorRuntimeError, match="requires"):
        method(None)
    assert runtime.processing_generation == 0
    first = bar(0)
    second = bar(1)
    method(first if entry_point == "process_bar" else runtime.aggregator.update(first))
    assert runtime.processing_generation == 1
    with pytest.raises(DetectorRuntimeError, match="scripted detector failure"):
        method(second if entry_point == "process_bar" else runtime.aggregator.update(second))
    assert runtime.processing_generation == 2
    with pytest.raises(DetectorRuntimeError, match="latched"):
        method(first)
    assert runtime.processing_generation == 2
    runtime.reset()
    assert runtime.processing_generation == 2
    assert runtime.reset_generation == 2
    method(first if entry_point == "process_bar" else runtime.aggregator.update(first))
    assert runtime.processing_generation == 3


def _assert_allowed(module: str) -> None:
    root, _, rest = module.partition(".")
    if root in sys.stdlib_module_names:
        return
    assert root == "market_analysis"
    if rest:
        assert rest.split(".", 1)[0] in {"config", "domain", "indicators", "patterns"}


def test_detection_package_has_no_ui_or_provider_dependencies() -> None:
    for path in Path("src/market_analysis/detection").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    _assert_allowed(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level or node.module is None:
                    continue  # relative imports stay inside the detection package
                _assert_allowed(node.module)


def test_only_the_application_layer_depends_on_detection() -> None:
    """SCRUM-61 wiring moved the runtime into application orchestration.

    The application layer composes use cases and is allowed to drive the
    SCRUM-80 detector runtime; domain, indicators, patterns, config,
    persistence, providers, api and demo modules still must not import it.
    """
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


def _continuation_intent(
    *,
    pattern_id: str = "trend-continuation",
    from_state: str = "candidate",
    to_state: str = "reclaimed",
    trigger_id: str = "ema_reclaim",
) -> TransitionIntent:
    return TransitionIntent(
        pattern_id=pattern_id,
        pattern_version="1",
        instance_id="trend-continuation",
        from_state=from_state,
        to_state=to_state,
        trigger_id=trigger_id,
        event_time=START + timedelta(minutes=5),
        detection_time=START + timedelta(minutes=5),
        rationale={"condition": trigger_id},
    )


def test_duplicate_trigger_on_one_bar_is_rejected() -> None:
    definition = continuation_definition()
    rogue = RogueIntentDetector(
        on_completed_bar=6,
        transitions=(_continuation_intent(), _continuation_intent()),
    )
    runtime = runtime_for([DetectorBinding(definition, rogue)], [definition])
    with pytest.raises(DetectorRuntimeError, match="already participates"):
        drive(runtime, chain_series())


def test_cross_binding_intent_forgery_is_rejected() -> None:
    definition = continuation_definition()
    rogue = RogueIntentDetector(
        on_completed_bar=6,
        transitions=(_continuation_intent(pattern_id="trend-reversal", to_state="confirmed"),),
    )
    runtime = runtime_for([DetectorBinding(definition, rogue)], [definition])
    with pytest.raises(DetectorRuntimeError, match="does not match the binding"):
        drive(runtime, chain_series())


def test_instance_state_jump_without_transitions_is_rejected() -> None:
    class JumpingDetector:
        def process_bar(self, bar_input: DetectorInput) -> DetectorOutput:
            return DetectorOutput(
                instance=replace(bar_input.instance, state="confirmed"),
            )

        def reset(self) -> None:
            return None

    definition = continuation_definition()
    runtime = runtime_for([DetectorBinding(definition, JumpingDetector())], [definition])
    with pytest.raises(DetectorRuntimeError, match="does not match the lifecycle state"):
        drive(runtime, chain_series())


def test_binding_fingerprint_distinguishes_registered_version_sets() -> None:
    v1 = continuation_definition()
    v2 = continuation_definition(version="2")
    config = config_with([pattern_selection(v1)])
    single = DetectorRuntime(
        config,
        [DetectorBinding(v1, continuation_detector())],
        run_id="run-80",
        dataset_revision_id="dataset-80",
    )
    both = DetectorRuntime(
        config,
        [
            DetectorBinding(v1, continuation_detector()),
            DetectorBinding(v2, continuation_detector(), instance_id="continuation-v2"),
        ],
        run_id="run-80",
        dataset_revision_id="dataset-80",
    )
    # The config hash covers only the SELECTED v1; the fingerprint must
    # distinguish what actually executes.
    assert single.detection_config_hash == both.detection_config_hash
    assert single.binding_fingerprint != both.binding_fingerprint
    twin = DetectorRuntime(
        config,
        [DetectorBinding(v1, continuation_detector())],
        run_id="other-run",
        dataset_revision_id="other-dataset",
    )
    assert twin.binding_fingerprint == single.binding_fingerprint


def test_process_frame_rejects_incomplete_bar_frames() -> None:
    runtime, _ = three_detector_runtime()
    frame = runtime.aggregator.update(bar(0))
    incomplete = replace(frame, bar=replace(frame.bar, is_complete=False))
    with pytest.raises(DetectorRuntimeError, match="completed"):
        runtime.process_frame(incomplete)
