from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest

from market_analysis.config import (
    ComponentSelection,
    ConfigParameter,
    DetectionAnalysisConfig,
    detection_config_hash,
    resolve_detection_config,
)
from market_analysis.domain import Bar, SessionWindow, Timeframe, TradingCalendar
from market_analysis.indicators import (
    IncrementalMarketState,
    MarketEvent,
    MarketEventType,
    MarketStateAggregator,
    MarketStateError,
    MarketStateFrame,
    QualificationEarned,
    QualificationStatus,
    StructureBreak,
    SwingPoint,
    TrendDirection,
    TrendLegState,
    TrendLegTransition,
)

START = datetime(2026, 1, 5, 12, tzinfo=UTC)
ALL_COMPONENTS = (
    "atr",
    "ema",
    "swing_point",
    "swing_structure",
    "trend_leg",
    "trend_leg_qualification",
    "range_state",
)

# Hand-reviewed real-chain series (UP direction). With ATR period 1 and a 0.1
# reversal multiplier, SwingPoints confirm on bars 6, 8, 10, 12, 14 and the
# high reference breaks on bar 13; a leg establishes on bar 14 and its
# protected low (price 89, buffer 2.10) breaks below 86.90 on the next bar.
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


def config(
    *,
    min_duration_bars: int = 4,
    min_move_points: str = "31",
    range_parameters: dict[str, object] | None = None,
    enabled: tuple[str, ...] = ALL_COMPONENTS,
) -> DetectionAnalysisConfig:
    selections: list[ComponentSelection] = []
    if "atr" in enabled:
        selections.append(ComponentSelection(
            component_id="atr", component_version="1",
            parameters=(ConfigParameter(name="period", value=1),),
        ))
    if "ema" in enabled:
        selections.append(ComponentSelection(
            component_id="ema", component_version="1",
            parameters=(ConfigParameter(name="period", value=3),),
        ))
    if "swing_point" in enabled:
        selections.append(ComponentSelection(
            component_id="swing_point", component_version="1",
            parameters=(
                ConfigParameter(name="atr_period", value=1),
                ConfigParameter(name="reversal_atr_multiplier", value=Decimal("0.1")),
            ),
        ))
    if "swing_structure" in enabled:
        selections.append(ComponentSelection(component_id="swing_structure", component_version="1"))
    if "trend_leg" in enabled:
        selections.append(ComponentSelection(component_id="trend_leg", component_version="1"))
    if "trend_leg_qualification" in enabled:
        selections.append(ComponentSelection(
            component_id="trend_leg_qualification", component_version="1",
            parameters=(
                ConfigParameter(name="min_duration_bars", value=min_duration_bars),
                ConfigParameter(name="min_directional_move_points", value=Decimal(min_move_points)),
            ),
        ))
    if "range_state" in enabled:
        selections.append(ComponentSelection(
            component_id="range_state", component_version="1",
            parameters=tuple(
                ConfigParameter(name=name, value=cast(Any, value))
                for name, value in (range_parameters or {}).items()
            ),
        ))
    return resolve_detection_config(DetectionAnalysisConfig(
        instrument_id="US30", timeframe=Timeframe.M1, calendar_id="cal-v1",
        components=tuple(selections),
    ))


def bar(index: int, close: str = "120", high: str = "125", low: str = "115") -> Bar:
    return Bar(
        "US30", Timeframe.M1, START + timedelta(minutes=index),
        Decimal(close), Decimal(high), Decimal(low), Decimal(close),
    )


def chain_series() -> tuple[Bar, ...]:
    return tuple(bar(i, "100", "101", "99") for i in range(5)) + tuple(
        bar(i, close, high, low) for i, (close, high, low) in enumerate(TRIPLES, 5)
    )


def aggregator(
    cfg: DetectionAnalysisConfig | None = None,
    **kwargs: object,
) -> MarketStateAggregator:
    options: dict[str, object] = {"run_id": "run", "dataset_revision_id": "dataset", **kwargs}
    return MarketStateAggregator(cfg or config(), **cast(Any, options))


def drive(
    state: MarketStateAggregator,
    series: tuple[Bar, ...],
) -> list[MarketStateFrame]:
    return [state.update(item) for item in series]


def component_of[ComponentT: IncrementalMarketState](
    state: MarketStateAggregator,
    kind: type[ComponentT],
) -> ComponentT:
    found = [item for item in state.components if isinstance(item, kind)]
    assert len(found) == 1
    return found[0]


def events_of(frame: MarketStateFrame, *types: MarketEventType) -> list[MarketEvent]:
    return [event for event in frame.market_events_this_bar if event.event_type in types]


def calendar() -> TradingCalendar:
    return TradingCalendar(
        calendar_id="cal-v1",
        version="fixture-v1",
        provider="seed",
        account="local",
        instrument_id="US30",
        timezone_name="Europe/London",
        trading_day_boundary=time(0),
        windows=(
            SessionWindow("london", time(8), time(11)),
            SessionWindow("pre_us", time(11), time(14)),
        ),
    )


def test_identical_sequence_serializes_identically_across_instances_and_runs() -> None:
    series = chain_series()
    first = drive(aggregator(), series)
    second = drive(aggregator(), series)
    assert [frame.debug_json() for frame in first] == [frame.debug_json() for frame in second]
    replay = aggregator()
    for index, item in enumerate(series):
        assert replay.update(item).debug_json() == first[index].debug_json()


def test_reset_replay_parity_over_full_frame_sequence() -> None:
    series = chain_series()
    state = aggregator()
    baseline = [frame.debug_json() for frame in drive(state, series)]
    state.reset()
    assert state.completed_bars == 0
    assert state.last_completed_bar is None
    assert [frame.debug_json() for frame in drive(state, series)] == baseline
    assert state.detection_config_hash == detection_config_hash(config())


def test_structure_break_visible_exactly_on_detection_bar() -> None:
    series = chain_series() + (bar(15, "121.5", "122", "119"),)
    frames = drive(aggregator(), series)
    for frame in frames[:13]:
        assert events_of(
            frame,
            MarketEventType.SWING_HIGH_CLOSE_BREAK,
            MarketEventType.SWING_LOW_CLOSE_BREAK,
        ) == []
    (event,) = events_of(frames[13], MarketEventType.SWING_HIGH_CLOSE_BREAK)
    evidence = cast(StructureBreak, event.evidence)
    assert event.event_time == event.detection_time == frames[13].bar.timestamp
    assert evidence.reference_price == Decimal(121)
    assert evidence.reference_atr == Decimal(22)
    assert evidence.buffer_points == Decimal("2.20")
    assert evidence.threshold == Decimal("123.20")
    assert evidence.breaking_close == Decimal(140)
    assert evidence.reference_swing.swing_index == 3
    assert evidence.reference_swing.detection_time == bar(10).timestamp
    for frame in (frames[14], frames[15]):
        assert events_of(
            frame,
            MarketEventType.SWING_HIGH_CLOSE_BREAK,
            MarketEventType.SWING_LOW_CLOSE_BREAK,
        ) == []


def test_trend_leg_transitions_visible_exactly_on_detection_bar() -> None:
    state = aggregator()
    frames = drive(state, chain_series())
    for frame in frames[:14]:
        assert events_of(
            frame,
            MarketEventType.TREND_LEG_STARTED,
            MarketEventType.TREND_LEG_QUALIFIED,
        ) == []
    started, = events_of(frames[14], MarketEventType.TREND_LEG_STARTED)
    qualified, = events_of(frames[14], MarketEventType.TREND_LEG_QUALIFIED)
    evidence = cast(TrendLegTransition, started.evidence)
    assert evidence.transition_type.value == "ESTABLISHED"
    assert evidence.leg.direction == TrendDirection.UP
    assert evidence.leg.initial_protected_swing.event_price == Decimal(89)
    # The leg's event time is its own structural anchor; detection is this bar.
    assert started.event_time == bar(11).timestamp
    assert started.detection_time == frames[14].bar.timestamp
    assert qualified.detection_time == frames[14].bar.timestamp
    breaking = bar(15, "86.89", "120", "86")
    ended_frame = state.update(breaking)
    ended, = events_of(ended_frame, MarketEventType.TREND_LEG_ENDED)
    qualification_ended, = events_of(ended_frame, MarketEventType.TREND_LEG_QUALIFICATION_ENDED)
    ended_evidence = cast(TrendLegTransition, ended.evidence)
    assert ended_evidence.transition_type.value == "TERMINATED"
    assert ended_evidence.leg.break_threshold == Decimal("86.90")
    assert ended_evidence.breaking_close == Decimal("86.89")
    ended_qualification = qualification_ended.evidence
    assert ended_qualification.evidence.status == QualificationStatus.QUALIFIED
    assert ended_qualification.evidence.first_earned is not None
    assert ended_qualification.source_transition is ended_evidence
    after = state.update(bar(16))
    assert events_of(
        after,
        MarketEventType.TREND_LEG_STARTED,
        MarketEventType.TREND_LEG_ENDED,
        MarketEventType.TREND_LEG_PROTECTION_ADVANCED,
        MarketEventType.TREND_LEG_QUALIFIED,
        MarketEventType.TREND_LEG_QUALIFICATION_ENDED,
    ) == []


def test_raw_and_qualified_trend_leg_state_are_distinguishable() -> None:
    state = aggregator(config(min_move_points="32"))
    frames = drive(state, chain_series())
    leg_frame = frames[14]
    assert leg_frame.availability["trend_leg"] == "ACTIVE"
    assert leg_frame.availability["trend_leg_qualification"] == "UNQUALIFIED"
    assert events_of(leg_frame, MarketEventType.TREND_LEG_STARTED)
    assert events_of(leg_frame, MarketEventType.TREND_LEG_QUALIFIED) == []
    evidence = cast(Any, leg_frame.components["trend_leg_qualification"])
    assert evidence["status"] == QualificationStatus.UNQUALIFIED
    active = evidence["active_qualification"]
    assert active["duration_gate_passed"] is True
    assert active["directional_move_gate_passed"] is False
    assert active["first_earned"] is None
    qualifies = state.update(bar(15, "121.5", "122", "119"))
    earned, = events_of(qualifies, MarketEventType.TREND_LEG_QUALIFIED)
    qualification = cast(QualificationEarned, earned.evidence)
    assert earned.event_time == earned.detection_time == qualifies.bar.timestamp
    assert qualification.source_leg.directional_movement_points == Decimal("32.5")
    assert qualifies.availability["trend_leg_qualification"] == "QUALIFIED"
    payload = cast(Any, qualifies.components["trend_leg_qualification"])
    first_earned = payload["active_qualification"]["first_earned"]
    assert first_earned["detection_time"] == earned.detection_time


def test_swing_point_event_and_detection_timing() -> None:
    series = chain_series() + (bar(15, "121.5", "122", "119"),)
    frames = drive(aggregator(), series)
    first_seen: dict[int, int] = {}
    for index, frame in enumerate(frames):
        for event in frame.market_events_this_bar:
            if event.event_type is not MarketEventType.SWING_POINT_CONFIRMED:
                continue
            evidence = cast(SwingPoint, event.evidence)
            assert evidence.swing_index not in first_seen
            assert evidence.event_time < evidence.detection_time == frame.bar.timestamp
            assert evidence.confirmation_bar_index == index
            first_seen[evidence.swing_index] = index
    assert first_seen == {1: 6, 2: 8, 3: 10, 4: 12, 5: 14, 6: 15}
    confirmed, = events_of(frames[14], MarketEventType.SWING_POINT_CONFIRMED)
    evidence = cast(SwingPoint, confirmed.evidence)
    assert evidence.swing_index == 5
    assert evidence.event_time == bar(13).timestamp
    assert evidence.event_price == Decimal(141)
    assert evidence.confirmation_close == Decimal(120)
    assert evidence.detection_time == bar(14).timestamp


def test_no_future_information_appears_before_detection_time() -> None:
    series = chain_series() + (bar(15, "121.5", "122", "119"),)
    frames = drive(aggregator(), series)
    for index, frame in enumerate(frames):
        item = frame.bar
        for event in frame.market_events_this_bar:
            assert event.detection_time == item.timestamp
            assert event.event_time <= item.timestamp
        for point in cast(Any, frame.components["swing_point"]["confirmed"]):
            assert point["detection_time"] <= item.timestamp
            assert point["confirmation_bar_index"] <= index
        for classification in cast(Any, frame.components["swing_structure"]["classifications"]):
            assert classification["source_swing"]["detection_time"] <= item.timestamp
        for transition in cast(Any, frame.components["trend_leg"]["current_bar_transitions"]):
            assert transition["detection_time"] == item.timestamp
        thresholds = cast(Any, frame.components["swing_structure"])
        for side in ("active_high_break_threshold", "active_low_break_threshold"):
            reference = thresholds["latest_high" if "high" in side else "latest_low"]
            if reference is not None:
                assert reference["source_swing"]["detection_time"] <= item.timestamp
    # The swing whose extreme printed on bar 13 is observable only from bar 14.
    assert not events_of(frames[13], MarketEventType.SWING_POINT_CONFIRMED)
    assert events_of(frames[14], MarketEventType.SWING_POINT_CONFIRMED)


def test_partial_warm_up_is_explicit_and_still_produces_frames() -> None:
    frames = drive(aggregator(), chain_series())
    assert dict(frames[0].availability) == {
        "ema": "WARMING_UP",
        "atr": "AVAILABLE",
        "swing_point": "WARMING_UP",
        "swing_structure": "UNDEFINED",
        "trend_leg": "NO_ACTIVE_LEG",
        "trend_leg_qualification": "NO_ACTIVE_LEG",
        "range_state": "WARMING_UP",
    }
    assert frames[0].completed_bars == 1
    assert set(frames[0].components) == set(ALL_COMPONENTS)
    # EMA(3) seeds on the third completed bar; SwingPoint search activates on
    # the sixth; the structural chain stays UNDEFINED until bar 7 classifies.
    assert frames[1].availability["ema"] == "WARMING_UP"
    assert cast(Any, frames[1].components["ema"])["value_ready"] is False
    assert frames[2].availability["ema"] == "AVAILABLE"
    assert cast(Any, frames[2].components["ema"])["value_ready"] is True
    assert frames[4].availability["swing_point"] == "WARMING_UP"
    assert frames[5].availability["swing_point"] == "AVAILABLE"
    assert frames[5].availability["swing_structure"] == "UNDEFINED"
    assert cast(Any, frames[5].components["range_state"]["chop"])["status"] == "WARMING_UP"


def test_partial_config_produces_frames_with_absent_families() -> None:
    cfg = config(enabled=("ema", "atr"))
    state = aggregator(cfg, pinned_config_hash=detection_config_hash(cfg))
    series = tuple(
        bar(index, str(100 + index), str(105 + index), str(95 + index)) for index in range(4)
    )
    frames = drive(state, series)
    assert set(frames[0].components) == {"ema", "atr"}
    assert dict(frames[0].availability) == {"ema": "WARMING_UP", "atr": "AVAILABLE"}
    assert frames[1].availability["ema"] == "WARMING_UP"
    assert dict(frames[3].availability) == {"ema": "AVAILABLE", "atr": "AVAILABLE"}
    for frame in frames:
        assert frame.market_events_this_bar == ()
        assert set(frame.availability) == {"ema", "atr"}
    assert all("trend_leg" not in frame.components for frame in frames)
    assert frames[3].pinned_config_hash == detection_config_hash(cfg)
    again = aggregator(cfg, pinned_config_hash=detection_config_hash(cfg))
    assert [frame.debug_json() for frame in drive(again, series)] == [
        frame.debug_json() for frame in frames
    ]


def test_range_state_partial_availability_and_degenerate_axes() -> None:
    state = aggregator(config(range_parameters={
        "chop_period": 3, "bandwidth_period": 2, "compression_reference_bars": 2,
    }))
    flat = tuple(bar(index, "120", "120", "120") for index in range(3))
    warming = drive(state, flat)
    assert warming[0].availability["range_state"] == "WARMING_UP"
    assert warming[1].availability["range_state"] == "WARMING_UP"
    assert warming[2].availability["range_state"] == "DEGENERATE"
    payload = cast(Any, warming[2].components["range_state"])
    assert payload["chop"]["status"] == "DEGENERATE"
    assert payload["chop"]["reason"] == "ZERO_WINDOW_RANGE"
    assert payload["chop"]["category"] == "UNAVAILABLE"
    assert payload["bandwidth_evidence"]["status"] == "AVAILABLE"
    assert payload["compression"]["status"] == "AVAILABLE"
    assert payload["compression"]["category"] == "COMPRESSED"
    moving = aggregator(config(range_parameters={
        "chop_period": 3, "bandwidth_period": 2, "compression_reference_bars": 2,
    }))
    frames = drive(moving, tuple(bar(index, "120", "125", "115") for index in range(3)))
    assert frames[1].availability["range_state"] == "WARMING_UP"
    assert cast(Any, frames[1].components["range_state"]["chop"])["status"] == "WARMING_UP"
    assert cast(Any, frames[1].components["range_state"]["bandwidth_evidence"])[
        "status"
    ] == "AVAILABLE"
    assert frames[2].availability["range_state"] == "AVAILABLE"
    assert cast(Any, frames[2].components["range_state"]["compression"])[
        "status"
    ] == "AVAILABLE"


def test_within_bar_event_ordering_is_deterministic() -> None:
    left = aggregator()
    right = aggregator()
    series = chain_series() + (bar(15, "86.89", "120", "86"),)
    for item in series:
        first = left.update(item)
        second = right.update(item)
        assert first.market_events_this_bar == second.market_events_this_bar
        ordinals = [event.ordinal for event in first.market_events_this_bar]
        assert ordinals == list(range(len(ordinals)))
    frames = drive(aggregator(), series)
    establishment = [
        (event.event_type, event.ordinal) for event in frames[14].market_events_this_bar
    ]
    assert establishment == [
        (MarketEventType.SWING_POINT_CONFIRMED, 0),
        (MarketEventType.SWING_STRUCTURE_CLASSIFIED, 1),
        (MarketEventType.EMA_CROSS, 2),
        (MarketEventType.TREND_LEG_STARTED, 3),
        (MarketEventType.TREND_LEG_QUALIFIED, 4),
    ]
    termination = [(event.event_type, event.ordinal) for event in frames[15].market_events_this_bar]
    assert termination == [
        (MarketEventType.SWING_LOW_CLOSE_BREAK, 0),
        (MarketEventType.TREND_LEG_ENDED, 1),
        (MarketEventType.TREND_LEG_QUALIFICATION_ENDED, 2),
    ]


def test_market_state_and_event_feed_are_immutable() -> None:
    state = aggregator()
    frame = drive(state, chain_series())[14]
    with pytest.raises(FrozenInstanceError):
        frame.bar = bar(0)  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        frame.completed_bars = 0  # type: ignore[misc]
    with pytest.raises(TypeError):  # slots dataclass unknown-attribute set
        frame.extra_field = 1  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        cast(Any, frame.components)["trend_leg"] = {}
    with pytest.raises(TypeError):
        cast(Any, frame.availability)["trend_leg"] = "ACTIVE"
    with pytest.raises(TypeError):
        cast(Any, frame.market_events_this_bar)[0] = None
    with pytest.raises(FrozenInstanceError):
        frame.market_events_this_bar = ()  # type: ignore[misc]
    payload = cast(Any, frame.components)
    with pytest.raises(TypeError):
        payload["swing_structure"]["latest_high"]["source_swing"]["event_price"] = Decimal(1)
    with pytest.raises(TypeError):
        payload["trend_leg"]["active_leg"]["duration_bars"] = 0
    event = frame.market_events_this_bar[3]
    with pytest.raises(FrozenInstanceError):
        event.ordinal = 9  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        cast(Any, event.evidence).leg.leg_index = 9
    state.reset()
    replayed = drive(state, chain_series())[14]
    assert replayed.market_events_this_bar[3].evidence == event.evidence


def test_consumer_reads_canonical_events_without_recomputation() -> None:
    class RecordingConsumer:
        """Fake detector boundary consumer; reads events, recomputes nothing."""

        def __init__(self) -> None:
            self.started: list[TrendLegTransition] = []
            self.ended: list[TrendLegTransition] = []
            self.qualified: list[QualificationEarned] = []
            self.breaks: list[StructureBreak] = []

        def consume(self, frame: MarketStateFrame) -> None:
            for event in frame.market_events_this_bar:
                if event.event_type is MarketEventType.TREND_LEG_STARTED:
                    assert isinstance(event.evidence, TrendLegTransition)
                    self.started.append(event.evidence)
                elif event.event_type is MarketEventType.TREND_LEG_ENDED:
                    assert isinstance(event.evidence, TrendLegTransition)
                    self.ended.append(event.evidence)
                elif event.event_type is MarketEventType.TREND_LEG_QUALIFIED:
                    assert isinstance(event.evidence, QualificationEarned)
                    self.qualified.append(event.evidence)
                elif event.event_type is MarketEventType.SWING_LOW_CLOSE_BREAK:
                    assert isinstance(event.evidence, StructureBreak)
                    self.breaks.append(event.evidence)

    consumer = RecordingConsumer()
    state = aggregator()
    leg = component_of(state, TrendLegState)
    series = chain_series()
    for item in series:
        frame = state.update(item)
        consumer.consume(frame)
        for transition in leg.current_bar_transitions:
            assert any(event.evidence is transition for event in frame.market_events_this_bar)
    assert len(consumer.started) == 1
    assert consumer.started[0].leg.direction == TrendDirection.UP
    assert consumer.started[0].leg.duration_bars == 4
    assert consumer.started[0].leg.directional_movement_points == Decimal(31)
    assert len(consumer.qualified) == 1
    assert consumer.qualified[0].thresholds.min_duration_bars == 4
    breaking = bar(15, "86.89", "120", "86")
    consumer.consume(state.update(breaking))
    assert len(consumer.ended) == 1
    assert consumer.ended[0].leg.break_threshold == Decimal("86.90")
    assert len(consumer.breaks) == 1
    assert consumer.breaks[0].threshold == Decimal("86.90")


def test_verified_pin_is_required_to_match_and_recorded_on_frames() -> None:
    cfg = config()
    with pytest.raises(MarketStateError, match="pinned_config_hash"):
        aggregator(cfg, pinned_config_hash="forged")
    pinned = detection_config_hash(cfg)
    state = aggregator(cfg, pinned_config_hash=pinned)
    frame = state.update(bar(0))
    assert state.pinned_config_hash == pinned
    assert frame.pinned_config_hash == pinned
    assert frame.detection_config_hash == pinned
    assert cast(Any, frame.components["swing_point"])["pinned_config_hash"] == pinned
    unpinned = aggregator()
    assert unpinned.pinned_config_hash is None
    assert unpinned.update(bar(0)).pinned_config_hash is None


def test_session_component_is_included_when_a_calendar_is_pinned() -> None:
    cfg = config(enabled=("ema", "atr"))
    state = aggregator(cfg, calendar=calendar(), pinned_calendar_version="fixture-v1")
    frame = state.update(bar(0))
    assert "session" in frame.components
    assert frame.availability["session"] == "AVAILABLE"
    assert frame.component_versions["session"] == "fixture-v1"
    payload = cast(Any, frame.components["session"])
    assert payload["calendar_version"] == "fixture-v1"
    assert payload["session_name"] == "pre_us"
    assert payload["is_open"] is True
    bare = aggregator(cfg)
    assert bare.session is None
    assert "session" not in bare.update(bar(0)).components
    with pytest.raises(MarketStateError, match="pinned version"):
        aggregator(cfg, calendar=calendar())
    with pytest.raises(MarketStateError, match="requires a calendar"):
        aggregator(cfg, pinned_calendar_version="fixture-v1")


def test_constructor_validates_config_and_lineage_identity() -> None:
    with pytest.raises(MarketStateError, match="run_config"):
        MarketStateAggregator(cast(Any, object()), run_id="run", dataset_revision_id="dataset")
    for identifiers in (
        {"run_id": "", "dataset_revision_id": "dataset"},
        {"run_id": "  ", "dataset_revision_id": "dataset"},
        {"run_id": "run", "dataset_revision_id": None},
    ):
        with pytest.raises(MarketStateError, match="nonempty"):
            aggregator(**cast(Any, identifiers))
    assert aggregator().run_config == config()


def test_update_rejects_invalid_bars_without_advancing() -> None:
    state = aggregator()
    with pytest.raises(MarketStateError, match="canonical Bar"):
        state.update(cast(Any, object()))
    with pytest.raises(MarketStateError, match="completed"):
        state.update(replace(bar(0), is_complete=False))
    with pytest.raises(MarketStateError, match="instrument/timeframe"):
        state.update(replace(bar(0), instrument_id="DAX"))
    state.update(bar(0))
    assert state.completed_bars == 1
    with pytest.raises(MarketStateError, match="strictly increasing"):
        state.update(bar(0))
    assert state.completed_bars == 1
    assert state.last_completed_bar == bar(0)


def test_debug_json_carries_diagnostic_evidence_and_lineage() -> None:
    state = aggregator()
    series = chain_series()
    frames = drive(state, series)
    payload = json.loads(frames[14].debug_json())
    assert payload["bar"]["timestamp"] == "2026-01-05T12:14:00Z"
    assert payload["completed_bars"] == 15
    assert payload["run_id"] == "run"
    assert payload["dataset_revision_id"] == "dataset"
    assert payload["detection_config_hash"] == detection_config_hash(config())
    assert payload["pinned_config_hash"] is None
    assert payload["component_versions"]["trend_leg"] == "1"
    assert payload["availability"]["trend_leg"] == "ACTIVE"
    structure = payload["components"]["swing_structure"]
    assert structure["active_high_break_threshold"] == "144.20"
    assert structure["latest_high"]["label"] == "HH"
    leg = payload["components"]["trend_leg"]["active_leg"]
    assert leg["break_threshold"] == "86.90"
    assert leg["duration_bars"] == 4
    assert leg["protected_swing"]["event_price"] == "89"
    qualification = payload["components"]["trend_leg_qualification"]
    assert qualification["status"] == "QUALIFIED"
    range_payload = payload["components"]["range_state"]
    assert payload["availability"]["range_state"] == "WARMING_UP"
    assert range_payload["chop"]["status"] == "AVAILABLE"
    assert range_payload["bandwidth_evidence"]["status"] == "WARMING_UP"
    events = payload["market_events_this_bar"]
    assert [event["event_type"] for event in events] == [
        "SWING_POINT_CONFIRMED",
        "SWING_STRUCTURE_CLASSIFIED",
        "EMA_CROSS",
        "TREND_LEG_STARTED",
        "TREND_LEG_QUALIFIED",
    ]
    assert [event["ordinal"] for event in events] == [0, 1, 2, 3, 4]
    assert events[3]["evidence"]["leg"]["break_threshold"] == "86.90"
    assert events[4]["evidence"]["transition_type"] == "QUALIFIED"
    pinned_state = aggregator(pinned_config_hash=detection_config_hash(config()))
    pinned_frame = pinned_state.update(bar(0))
    assert json.loads(pinned_frame.debug_json())["pinned_config_hash"] == detection_config_hash(
        config()
    )


def test_instance_id_session_is_reserved() -> None:
    cfg = resolve_detection_config(DetectionAnalysisConfig(
        instrument_id="US30", timeframe=Timeframe.M1, calendar_id="cal-v1",
        components=(
            ComponentSelection(
                component_id="ema", component_version="1", instance_id="session",
                parameters=(ConfigParameter(name="period", value=3),),
            ),
        ),
    ))
    with pytest.raises(MarketStateError, match="reserved"):
        aggregator(cfg)
    with pytest.raises(MarketStateError, match="reserved"):
        aggregator(cfg, calendar=calendar(), pinned_calendar_version="fixture-v1")


def test_unresolved_config_is_expanded_without_keyerrors() -> None:
    unresolved = DetectionAnalysisConfig(
        instrument_id="US30", timeframe=Timeframe.M1, calendar_id="cal-v1",
        components=tuple(
            ComponentSelection(component_id=name, component_version="1")
            for name in ALL_COMPONENTS
        ),
    )
    state = aggregator(unresolved)
    assert state.run_config == resolve_detection_config(unresolved)
    frame = state.update(bar(0))
    assert set(frame.components) == set(ALL_COMPONENTS)
    assert frame.detection_config_hash == detection_config_hash(
        resolve_detection_config(unresolved)
    )


def test_multiple_instances_order_events_by_instance_id() -> None:
    cfg = resolve_detection_config(DetectionAnalysisConfig(
        instrument_id="US30", timeframe=Timeframe.M1, calendar_id="cal-v1",
        components=(
            ComponentSelection(component_id="atr", component_version="1",
                               parameters=(ConfigParameter(name="period", value=1),)),
            ComponentSelection(component_id="ema", component_version="1",
                               parameters=(ConfigParameter(name="period", value=3),)),
            ComponentSelection(component_id="ema", component_version="1", instance_id="slow",
                               parameters=(ConfigParameter(name="period", value=3),)),
            ComponentSelection(component_id="swing_point", component_version="1",
                               parameters=(ConfigParameter(name="atr_period", value=1),
                                           ConfigParameter(
                                               name="reversal_atr_multiplier",
                                               value=Decimal("0.1"),
                                           ))),
            ComponentSelection(component_id="swing_structure", component_version="1"),
            ComponentSelection(component_id="trend_leg", component_version="1",
                               parameters=(ConfigParameter(
                                   name="ema_instance_id", value="ema",
                               ),)),
            ComponentSelection(component_id="trend_leg", component_version="1", instance_id="aux",
                               parameters=(ConfigParameter(
                                   name="ema_instance_id", value="slow",
                               ),)),
        ),
    ))
    state = aggregator(cfg)
    series = tuple(bar(i, "100", "101", "99") for i in range(3)) + (bar(3, "130", "131", "99"),)
    frames = drive(state, series)
    assert [frame.market_events_this_bar for frame in frames[:3]] == [()] * 3
    crosses = [
        event for event in frames[3].market_events_this_bar
        if event.event_type is MarketEventType.EMA_CROSS
    ]
    assert [event.source_instance_id for event in crosses] == ["aux", "trend_leg"]
    assert [event.ordinal for event in crosses] == [0, 1]
    assert crosses[0].evidence is not crosses[1].evidence


def test_zero_component_config_yields_wellformed_empty_frames() -> None:
    cfg = resolve_detection_config(DetectionAnalysisConfig(
        instrument_id="US30", timeframe=Timeframe.M1, calendar_id="cal-v1",
    ))
    series = tuple(bar(index) for index in range(3))
    frames = drive(aggregator(cfg), series)
    for frame in frames:
        assert dict(frame.components) == {}
        assert dict(frame.availability) == {}
        assert frame.market_events_this_bar == ()
    replayed = drive(aggregator(cfg), series)
    assert [frame.debug_json() for frame in frames] == [
        frame.debug_json() for frame in replayed
    ]


def test_down_direction_leg_events_flow_through_the_aggregator() -> None:
    # Price-mirror (p -> 240 - p, high/low swapped) of the UP chain: every
    # component is difference-symmetric, so the DOWN leg establishes on bar 14
    # with the mirrored protected high 151 and breaks above 153.10 on bar 15.
    mirrored = tuple(
        (
            str(Decimal(240) - Decimal(close)),
            str(Decimal(240) - Decimal(low)),
            str(Decimal(240) - Decimal(high)),
        )
        for close, high, low in TRIPLES
    )
    series = tuple(bar(i, "140", "141", "139") for i in range(5)) + tuple(
        bar(i, close, high, low) for i, (close, high, low) in enumerate(mirrored, 5)
    )
    state = aggregator()
    frames = drive(state, series)
    for frame in frames[:14]:
        assert events_of(frame, MarketEventType.TREND_LEG_STARTED) == []
        assert events_of(frame, MarketEventType.TREND_LEG_ENDED) == []
    started, = events_of(frames[14], MarketEventType.TREND_LEG_STARTED)
    started_evidence = cast(TrendLegTransition, started.evidence)
    assert started_evidence.leg.direction == TrendDirection.DOWN
    assert started_evidence.leg.initial_protected_swing.event_price == Decimal(151)
    qualified, = events_of(frames[14], MarketEventType.TREND_LEG_QUALIFIED)
    qualification = cast(QualificationEarned, qualified.evidence)
    assert qualification.source_leg.directional_movement_points == Decimal("31")
    breaking = bar(15, "153.11", "154", "120")
    ended_frame = state.update(breaking)
    assert [
        (event.event_type, event.ordinal) for event in ended_frame.market_events_this_bar
    ] == [
        (MarketEventType.SWING_HIGH_CLOSE_BREAK, 0),
        (MarketEventType.TREND_LEG_ENDED, 1),
        (MarketEventType.TREND_LEG_QUALIFICATION_ENDED, 2),
    ]
    break_event, ended, _ = ended_frame.market_events_this_bar
    assert cast(StructureBreak, break_event.evidence).threshold == Decimal("153.10")
    assert cast(TrendLegTransition, ended.evidence).leg.break_threshold == Decimal("153.10")
    assert cast(TrendLegTransition, ended.evidence).breaking_close == Decimal("153.11")
