"""SCRUM-85 executable frozen-reference continuation lifecycle boundaries."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any, cast

import pytest
from test_detector_runtime import bar, chain_series, config_with, pattern_selection

from market_analysis.config import ConfigParameter, PatternSelection
from market_analysis.detection import (
    CONTINUATION_V1,
    REVERSAL_V1,
    ContinuationDetector,
    DetectorBinding,
    DetectorEvent,
    DetectorRuntime,
    ReversalDetector,
)
from market_analysis.domain import Bar
from market_analysis.indicators import (
    MarketEventType,
    OverallStructure,
    StructureBreak,
    market_event_semantic_ref,
)
from market_analysis.patterns import validate_event_evidence
from market_analysis.patterns.instance_context import encode_context


def _runtime(max_age: int = 60) -> DetectorRuntime:
    selection = (
        pattern_selection(CONTINUATION_V1)
        if max_age == 60
        else PatternSelection(
            pattern_id=CONTINUATION_V1.pattern_id,
            pattern_version=CONTINUATION_V1.pattern_version,
            parameters=(ConfigParameter(name="max_candidate_age_bars", value=max_age),),
        )
    )
    return DetectorRuntime(
        config_with([selection]),
        [DetectorBinding(CONTINUATION_V1, ContinuationDetector(), reentrant=True)],
        run_id="run-85",
        dataset_revision_id="dataset-85",
    )


def _drive_candidate(runtime: DetectorRuntime) -> None:
    for item in chain_series():
        runtime.process_bar(item)
    assert runtime.instances[0].state == "CANDIDATE"


def _mirror(item: Bar) -> Bar:
    return replace(
        item,
        open=Decimal(200) - item.open,
        high=Decimal(200) - item.low,
        low=Decimal(200) - item.high,
        close=Decimal(200) - item.close,
    )


def _features(event: DetectorEvent) -> Any:
    return cast(Any, event.rationale["items"])[0]["features"]


def test_aligned_qualified_up_leg_confirms_on_same_bar_reclaim_and_frozen_high_break() -> None:
    runtime = _runtime()
    _drive_candidate(runtime)
    candidate = runtime.events[0]
    assert candidate.trigger_id == "opposing_ema_cross"
    assert candidate.detection_time == bar(14).timestamp
    context = runtime.instances[0].context
    assert context["source_direction"] == "UP"
    assert context["continuation_swing_index"] == 5
    assert context["protected_swing_index"] == 4
    assert context["max_pullback_depth_points"] == Decimal(22)
    assert context["source_ema_instance_id"] == "ema"
    assert context["source_ema_period"] == 3
    encode_context(CONTINUATION_V1, context)

    confirmed = runtime.process_bar(bar(15, "150", "160", "119"))
    assert [(event.trigger_id, event.to_state, event.sequence) for event in confirmed.events] == [
        ("ema_reclaim", "RECLAIMED", 1),
        ("continuation_break", "CONFIRMED", 2),
    ]
    assert all(event.detection_time == bar(15).timestamp for event in confirmed.events)
    structure_break = next(
        event
        for event in confirmed.frame.market_events_this_bar
        if event.event_type is MarketEventType.SWING_HIGH_CLOSE_BREAK
    )
    assert confirmed.events[1].event_time == structure_break.event_time
    assert confirmed.events[1].rationale["source_market_event_refs"] == (
        market_event_semantic_ref(confirmed.frame, structure_break),
    )
    features = _features(confirmed.events[1])
    assert features["bars_candidate_to_confirmation"]["value"] == 1
    assert features["bars_reclaim_to_confirmation"]["value"] == 0
    assert features["reaction_swing_label"]["value"] == "HL"
    assert features["reaction_swing_count"]["value"] == 1
    assert features["max_pullback_depth_before_reclaim"]["value"] == "22"
    assert features["pullback_depth_in_atr_atr"]["type"] == "decimal"
    for event in runtime.events:
        validate_event_evidence(CONTINUATION_V1, event.trigger_id, event.rationale)
    assert runtime.occurrences[0].state == "CONFIRMED"
    encode_context(CONTINUATION_V1, runtime.occurrences[0].context)


def test_mirrored_down_leg_confirms_with_lh_evidence() -> None:
    runtime = _runtime()
    series = (*chain_series(), bar(15, "150", "160", "119"))
    results = [runtime.process_bar(_mirror(item)) for item in series]
    assert results[14].instances[0].context["source_direction"] == "DOWN"
    assert results[14].instances[0].context["continuation_swing_index"] == 6
    assert [event.trigger_id for event in results[15].events] == [
        "ema_reclaim",
        "continuation_break",
    ]
    assert results[15].instances[0].state == "CONFIRMED"
    assert _features(results[15].events[1])["reaction_swing_label"]["value"] == "LH"


def test_reclaim_then_later_frozen_high_break_confirms_without_retroactive_event() -> None:
    runtime = _runtime()
    _drive_candidate(runtime)
    reclaimed = runtime.process_bar(bar(15, "130", "131", "119"))
    assert [(event.trigger_id, event.to_state) for event in reclaimed.events] == [
        ("ema_reclaim", "RECLAIMED")
    ]
    assert reclaimed.instances[0].state == "RECLAIMED"
    confirmed = runtime.process_bar(bar(16, "150", "151", "129"))
    assert [(event.trigger_id, event.to_state) for event in confirmed.events] == [
        ("continuation_break", "CONFIRMED")
    ]
    assert confirmed.events[0].event_time == bar(16).timestamp
    features = _features(confirmed.events[0])
    assert features["bars_candidate_to_confirmation"]["value"] == 2
    assert features["bars_reclaim_to_confirmation"]["value"] == 1
    assert features["ema_slope"]["type"] == "decimal"


def test_reversal_and_continuation_candidates_can_coexist_from_one_cross() -> None:
    runtime = DetectorRuntime(
        config_with([pattern_selection(REVERSAL_V1), pattern_selection(CONTINUATION_V1)]),
        [
            DetectorBinding(REVERSAL_V1, ReversalDetector(), reentrant=True),
            DetectorBinding(CONTINUATION_V1, ContinuationDetector(), reentrant=True),
        ],
        run_id="joint-85",
        dataset_revision_id="dataset-85",
    )
    results = [runtime.process_bar(item) for item in chain_series()]
    candidate_events = results[14].events
    assert {event.pattern_id for event in candidate_events} == {
        REVERSAL_V1.pattern_id,
        CONTINUATION_V1.pattern_id,
    }
    assert all(event.to_state == "CANDIDATE" for event in candidate_events)
    result = runtime.process_bar(bar(15, "150", "160", "119"))
    states = {instance.pattern_id: instance.state for instance in result.instances}
    assert states[CONTINUATION_V1.pattern_id] == "CONFIRMED"
    assert states[REVERSAL_V1.pattern_id] == "CANDIDATE"


def test_frozen_protected_low_break_invalidates_before_reclaim() -> None:
    runtime = _runtime()
    _drive_candidate(runtime)
    invalidated = runtime.process_bar(bar(15, "70", "110", "69"))
    assert [(event.trigger_id, event.to_state) for event in invalidated.events] == [
        ("protected_swing_break", "INVALIDATED")
    ]
    assert _features(invalidated.events[0])["reference_swing_index_at_break"]["value"] == 4
    assert invalidated.instances[0].context["protected_swing_index"] == 4


def test_misaligned_structure_does_not_open_candidate() -> None:
    runtime = _runtime()
    for item in chain_series()[:14]:
        runtime.process_bar(item)
    frame = runtime.aggregator.update(chain_series()[14])
    components = dict(frame.components)
    components["swing_structure"] = {
        **components["swing_structure"],
        "overall_structure": OverallStructure.MIXED,
    }
    result = runtime.process_frame(replace(frame, components=components))
    assert result.instances[0].state == "INACTIVE"
    assert not result.events


def test_unqualified_crosses_before_aligned_leg_do_not_open_candidate() -> None:
    runtime = _runtime()
    for item in chain_series()[:14]:
        result = runtime.process_bar(item)
        assert result.instances[0].state == "INACTIVE"
        assert not result.events


def test_missing_confirmed_swing_reference_does_not_open_candidate() -> None:
    runtime = _runtime()
    for item in chain_series()[:14]:
        runtime.process_bar(item)
    frame = runtime.aggregator.update(chain_series()[14])
    components = dict(frame.components)
    components["swing_structure"] = {**components["swing_structure"], "latest_high": None}
    result = runtime.process_frame(replace(frame, components=components))
    assert result.instances[0].state == "INACTIVE"
    assert not result.events


def test_same_direction_break_before_reclaim_does_not_confirm() -> None:
    runtime = _runtime()
    _drive_candidate(runtime)
    frame = runtime.aggregator.update(bar(15, "150", "160", "119"))
    assert any(
        event.event_type is MarketEventType.SWING_HIGH_CLOSE_BREAK
        for event in frame.market_events_this_bar
    )
    result = runtime.process_frame(
        replace(
            frame,
            market_events_this_bar=tuple(
                event
                for event in frame.market_events_this_bar
                if event.event_type is not MarketEventType.EMA_CROSS
            ),
        )
    )
    assert result.instances[0].state == "CANDIDATE"
    assert not result.events


def test_newer_break_and_source_leg_end_do_not_replace_frozen_protected_reference() -> None:
    runtime = _runtime()
    _drive_candidate(runtime)
    frame = runtime.aggregator.update(bar(15, "150", "160", "119"))
    # Isolate a reclaim without its matching break. The resulting canonical
    # aggregator state then produces a *newer* low reference on the next bar.
    reclaimed = runtime.process_frame(
        replace(
            frame,
            market_events_this_bar=tuple(
                event
                for event in frame.market_events_this_bar
                if event.event_type is not MarketEventType.SWING_HIGH_CLOSE_BREAK
            ),
        )
    )
    assert reclaimed.instances[0].state == "RECLAIMED"
    result = runtime.process_bar(bar(16, "70", "160", "69"))
    newer_low_break = next(
        event
        for event in result.frame.market_events_this_bar
        if event.event_type is MarketEventType.SWING_LOW_CLOSE_BREAK
    )
    assert isinstance(newer_low_break.evidence, StructureBreak)
    assert newer_low_break.evidence.reference_swing.swing_index == 6
    assert any(
        event.event_type is MarketEventType.TREND_LEG_ENDED
        for event in result.frame.market_events_this_bar
    )
    assert result.instances[0].context["protected_swing_index"] == 4
    assert result.instances[0].state == "RECLAIMED"
    assert not result.events


def test_post_reclaim_break_of_newer_high_does_not_confirm() -> None:
    runtime = _runtime()
    _drive_candidate(runtime)
    assert runtime.process_bar(bar(15, "130", "131", "119")).instances[0].state == "RECLAIMED"
    frame = runtime.aggregator.update(bar(16, "150", "151", "129"))
    breaks = [
        event
        for event in frame.market_events_this_bar
        if event.event_type is MarketEventType.SWING_HIGH_CLOSE_BREAK
    ]
    assert len(breaks) == 1
    original = breaks[0]
    assert isinstance(original.evidence, StructureBreak)
    newer_swing = replace(original.evidence.reference_swing, swing_index=999)
    changed_break = replace(
        original,
        evidence=replace(original.evidence, reference_swing=newer_swing),
    )
    result = runtime.process_frame(
        replace(
            frame,
            market_events_this_bar=tuple(
                changed_break if event is original else event
                for event in frame.market_events_this_bar
            ),
        )
    )
    assert result.instances[0].state == "RECLAIMED"
    assert not result.events


def test_reclaimed_candidate_invalidates_on_exact_frozen_protected_break() -> None:
    runtime = _runtime()
    _drive_candidate(runtime)
    runtime.process_bar(bar(15, "130", "131", "119"))
    frame = runtime.aggregator.update(bar(16, "70", "130", "69"))
    low_break = next(
        event
        for event in frame.market_events_this_bar
        if event.event_type is MarketEventType.SWING_LOW_CLOSE_BREAK
    )
    assert isinstance(low_break.evidence, StructureBreak)
    assert low_break.evidence.reference_swing.swing_index != 4

    alternate = _runtime()
    _drive_candidate(alternate)
    old_frame = alternate.aggregator.update(bar(15, "70", "110", "69"))
    old_break = next(
        event
        for event in old_frame.market_events_this_bar
        if event.event_type is MarketEventType.SWING_LOW_CLOSE_BREAK
    )
    assert isinstance(old_break.evidence, StructureBreak)
    matched = replace(
        low_break,
        evidence=replace(
            low_break.evidence,
            reference_swing=old_break.evidence.reference_swing,
            reference_price=old_break.evidence.reference_price,
            threshold=old_break.evidence.threshold,
        ),
    )
    result = runtime.process_frame(
        replace(
            frame,
            market_events_this_bar=tuple(
                matched if event is low_break else event for event in frame.market_events_this_bar
            ),
        )
    )
    assert [(event.from_state, event.to_state) for event in result.events] == [
        ("RECLAIMED", "INVALIDATED")
    ]


def test_protected_break_wins_when_reclaim_and_continuation_break_share_bar() -> None:
    runtime = _runtime()
    _drive_candidate(runtime)
    frame = runtime.aggregator.update(bar(15, "150", "160", "119"))
    alternate = _runtime()
    _drive_candidate(alternate)
    low_frame = alternate.aggregator.update(bar(15, "70", "110", "69"))
    protected = next(
        event
        for event in low_frame.market_events_this_bar
        if event.event_type is MarketEventType.SWING_LOW_CLOSE_BREAK
    )
    injected = replace(protected, ordinal=len(frame.market_events_this_bar))
    result = runtime.process_frame(
        replace(frame, market_events_this_bar=(*frame.market_events_this_bar, injected))
    )
    assert [event.trigger_id for event in result.events] == ["protected_swing_break"]
    assert result.instances[0].state == "INVALIDATED"


def test_sixtieth_subsequent_bar_expires_after_reclaim_opportunity() -> None:
    runtime = _runtime()
    _drive_candidate(runtime)
    for index in range(15, 74):
        result = runtime.process_bar(bar(index, "120", "121", "119"))
        assert result.instances[0].state == "CANDIDATE"
        assert not result.events
    result = runtime.process_bar(bar(74, "120", "121", "119"))
    assert [(event.trigger_id, event.to_state) for event in result.events] == [
        ("candidate_age_limit", "EXPIRED")
    ]
    assert result.events[0].event_time == bar(74).timestamp


def test_ema_recross_alone_does_not_invalidate_reclaimed_candidate() -> None:
    runtime = _runtime()
    _drive_candidate(runtime)
    runtime.process_bar(bar(15, "130", "131", "119"))
    result = runtime.process_bar(bar(16, "110", "131", "109"))
    assert any(
        event.event_type is MarketEventType.EMA_CROSS
        for event in result.frame.market_events_this_bar
    )
    assert result.instances[0].state == "RECLAIMED"
    assert not result.events


def test_reclaimed_candidate_expires_and_protected_break_preempts_expiry() -> None:
    expiring = _runtime(max_age=2)
    _drive_candidate(expiring)
    expiring.process_bar(bar(15, "130", "131", "119"))
    assert expiring.process_bar(bar(16, "130", "131", "119")).instances[0].state == "EXPIRED"

    protected = _runtime(max_age=1)
    _drive_candidate(protected)
    result = protected.process_bar(bar(15, "70", "110", "69"))
    assert result.instances[0].state == "INVALIDATED"
    assert [event.trigger_id for event in result.events] == ["protected_swing_break"]


def test_reclaim_on_expiry_bar_without_break_expires_on_that_bar() -> None:
    runtime = _runtime(max_age=1)
    _drive_candidate(runtime)
    result = runtime.process_bar(bar(15, "130", "131", "119"))
    assert [(event.trigger_id, event.to_state) for event in result.events] == [
        ("ema_reclaim", "RECLAIMED"),
        ("candidate_age_limit", "EXPIRED"),
    ]
    assert result.instances[0].state == "EXPIRED"
    assert result.events[-1].detection_time == bar(15).timestamp


def test_age_limit_configuration_changes_hash_and_expiry() -> None:
    assert _runtime().detection_config_hash != _runtime(max_age=2).detection_config_hash
    runtime = _runtime(max_age=2)
    _drive_candidate(runtime)
    assert runtime.process_bar(bar(15, "120", "121", "119")).instances[0].state == "CANDIDATE"
    assert runtime.process_bar(bar(16, "120", "121", "119")).instances[0].state == "EXPIRED"
    with pytest.raises(ValueError, match="max_candidate_age_bars"):
        _runtime(max_age=0)


def test_reset_replay_is_byte_identical_and_terminal_rearms() -> None:
    runtime = _runtime()
    series = (*chain_series(), bar(15, "150", "160", "119"), bar(16, "150", "151", "149"))
    results = [runtime.process_bar(item) for item in series]
    assert results[16].instances[0].state == "INACTIVE"
    assert results[16].instances[0].instance_id.endswith(":1")
    before = runtime.debug_json()
    runtime.reset()
    for item in series:
        runtime.process_bar(item)
    assert runtime.debug_json() == before
