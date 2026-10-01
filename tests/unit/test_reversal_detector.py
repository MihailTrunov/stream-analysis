"""SCRUM-83 real-chain reversal examples and strict evidence boundaries."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from test_detector_runtime import bar, chain_series, config_with, pattern_selection

from market_analysis.config import ConfigParameter, PatternSelection
from market_analysis.detection import (
    REVERSAL_V1,
    DetectorBinding,
    DetectorInput,
    DetectorRuntime,
    ReversalDetector,
)
from market_analysis.domain import Bar
from market_analysis.indicators import OverallStructure, market_event_semantic_ref
from market_analysis.patterns import validate_event_evidence
from market_analysis.patterns.instance_context import encode_context


def _runtime(max_age: int = 60) -> DetectorRuntime:
    selection = (
        pattern_selection(REVERSAL_V1)
        if max_age == 60
        else PatternSelection(
            pattern_id=REVERSAL_V1.pattern_id,
            pattern_version=REVERSAL_V1.pattern_version,
            parameters=(ConfigParameter(name="max_candidate_age_bars", value=max_age),),
        )
    )
    return DetectorRuntime(
        config_with([selection]),
        [DetectorBinding(REVERSAL_V1, ReversalDetector(), reentrant=True)],
        run_id="run-80",
        dataset_revision_id="dataset-80",
    )


def test_qualified_aligned_up_leg_cross_then_own_protected_low_break() -> None:
    runtime = _runtime()
    for item in chain_series()[:14]:
        assert not runtime.process_bar(item).events
    candidate = runtime.process_bar(chain_series()[14])
    assert candidate.instances[0].state == "CANDIDATE"
    assert [(item.trigger_id, item.to_state) for item in candidate.events] == [
        ("opposing_ema_cross", "CANDIDATE")
    ]
    context = candidate.instances[0].context
    assert context["source_direction"] == "UP"
    assert context["candidate_direction"] == "DOWN"
    assert context["protected_swing_index"] == 4
    encode_context(REVERSAL_V1, context)
    candidate_features = candidate.events[0].rationale["items"][0]["features"]
    assert candidate_features["source_leg_ref"]["value"] == context["source_leg_ref"]
    assert candidate_features["source_duration_bars"]["type"] == "integer"
    assert candidate_features["current_ema"]["type"] == "decimal"
    confirmed = runtime.process_bar(bar(15, "70", "110", "69"))
    assert confirmed.frame.components["trend_leg"]["active_leg"] is None
    assert (
        confirmed.frame.components["swing_structure"]["overall_structure"]
        is OverallStructure.BULLISH
    )
    assert [(item.trigger_id, item.to_state) for item in confirmed.events] == [
        ("protected_swing_break", "CONFIRMED")
    ]
    assert confirmed.events[0].event_time == confirmed.frame.bar.timestamp
    assert confirmed.events[0].sequence == 1
    confirmation_features = confirmed.events[0].rationale["items"][0]["features"]
    assert confirmation_features["bars_since_candidate"]["value"] == 1
    assert confirmation_features["pre_break_structure"]["value"] == "BULLISH"
    assert confirmation_features["close_ema_side"]["value"] == "BELOW"
    assert confirmation_features["protected_swing_price_at_break"]["value"] == "89"
    for event in runtime.events:
        validate_event_evidence(REVERSAL_V1, event.trigger_id, event.rationale)
    assert len(runtime.occurrences) == 1
    assert runtime.occurrences[0].state == "CONFIRMED"
    encode_context(REVERSAL_V1, runtime.occurrences[0].context)


def test_terminal_occurrence_does_not_reenter_without_fresh_cross() -> None:
    runtime = _runtime()
    for item in (*chain_series(), bar(15, "70", "110", "69")):
        runtime.process_bar(item)
    next_bar = runtime.process_bar(bar(16, "70", "71", "69"))
    assert next_bar.instances[0].instance_id == f"{REVERSAL_V1.pattern_id}:1"
    assert next_bar.instances[0].state == "INACTIVE"
    assert not next_bar.events
    assert len(runtime.occurrences) == 1


def test_mirrored_down_leg_confirms_on_own_protected_high_break() -> None:
    runtime = _runtime()
    original = (*chain_series(), bar(15, "70", "110", "69"))
    mirrored = tuple(
        replace(
            item,
            open=Decimal(200) - item.open,
            high=Decimal(200) - item.low,
            low=Decimal(200) - item.high,
            close=Decimal(200) - item.close,
        )
        for item in original
    )
    results = [runtime.process_bar(item) for item in mirrored]
    assert results[14].instances[0].context["source_direction"] == "DOWN"
    assert results[14].instances[0].context["candidate_direction"] == "UP"
    assert results[15].frame.components["trend_leg"]["active_leg"] is None
    assert [event.trigger_id for event in runtime.events] == [
        "opposing_ema_cross",
        "protected_swing_break",
    ]
    assert runtime.instances[0].state == "CONFIRMED"


def test_ema_recross_alone_does_not_invalidate_but_new_hh_does() -> None:
    runtime = _runtime()
    for item in chain_series():
        runtime.process_bar(item)
    recross = runtime.process_bar(bar(15, "150", "160", "119"))
    assert any(event.event_type == "EMA_CROSS" for event in recross.frame.market_events_this_bar)
    assert recross.instances[0].state == "CANDIDATE"
    assert not recross.events
    extreme = runtime.process_bar(bar(16, "130", "150", "119"))
    assert [(event.trigger_id, event.to_state) for event in extreme.events] == [
        ("same_direction_extreme", "INVALIDATED")
    ]
    assert extreme.events[0].event_time < extreme.events[0].detection_time
    assert (
        extreme.events[0].rationale["items"][0]["features"]["invalidating_swing_index"]["type"]
        == "integer"
    )


def test_mirrored_new_ll_invalidates_bearish_source_after_recross() -> None:
    def mirror(item: Bar) -> Bar:
        return replace(
            item,
            open=Decimal(200) - item.open,
            high=Decimal(200) - item.low,
            low=Decimal(200) - item.high,
            close=Decimal(200) - item.close,
        )

    runtime = _runtime()
    for item in chain_series():
        runtime.process_bar(mirror(item))
    recross = runtime.process_bar(mirror(bar(15, "150", "160", "119")))
    assert recross.instances[0].state == "CANDIDATE"
    assert not recross.events
    extreme = runtime.process_bar(mirror(bar(16, "130", "150", "119")))
    assert [(event.trigger_id, event.to_state) for event in extreme.events] == [
        ("same_direction_extreme", "INVALIDATED")
    ]
    assert extreme.events[0].event_time < extreme.events[0].detection_time
    assert extreme.instances[0].context["source_direction"] == "DOWN"


def test_source_leg_end_wins_over_new_hh_and_newer_structure_break() -> None:
    runtime = _runtime()
    for item in chain_series():
        runtime.process_bar(item)
    runtime.process_bar(bar(15, "150", "160", "119"))
    result = runtime.process_bar(bar(16, "70", "160", "69"))
    assert {event.event_type for event in result.frame.market_events_this_bar} >= {
        "SWING_STRUCTURE_CLASSIFIED",
        "SWING_LOW_CLOSE_BREAK",
        "TREND_LEG_ENDED",
    }
    assert [(event.trigger_id, event.to_state) for event in result.events] == [
        ("protected_swing_break", "CONFIRMED")
    ]
    end_event = next(
        event
        for event in result.frame.market_events_this_bar
        if event.event_type == "TREND_LEG_ENDED"
    )
    structure_break = next(
        event
        for event in result.frame.market_events_this_bar
        if event.event_type == "SWING_LOW_CLOSE_BREAK"
    )
    assert end_event.evidence.leg.protected_swing.swing_index == 4
    assert structure_break.evidence.reference_swing.swing_index == 6
    assert result.events[0].rationale["source_market_event_refs"] == (
        market_event_semantic_ref(result.frame, end_event),
    )


def test_candidate_expires_on_sixtieth_subsequent_bar_not_before() -> None:
    runtime = _runtime()
    for item in chain_series():
        runtime.process_bar(item)
    for index in range(15, 74):
        result = runtime.process_bar(bar(index, "120", "121", "119"))
        assert result.instances[0].state == "CANDIDATE"
        assert not result.events
    expiry = runtime.process_bar(bar(74, "120", "121", "119"))
    assert expiry.instances[0].state == "EXPIRED"
    assert [(event.trigger_id, event.to_state) for event in expiry.events] == [
        ("candidate_age_limit", "EXPIRED")
    ]
    assert not expiry.events[0].rationale["source_market_event_refs"]
    encode_context(REVERSAL_V1, runtime.occurrences[0].context)


def test_source_protection_break_on_expiry_bar_confirms_before_age_limit() -> None:
    runtime = _runtime()
    for item in chain_series():
        runtime.process_bar(item)
    for index in range(15, 74):
        runtime.process_bar(bar(index, "120", "121", "119"))
    result = runtime.process_bar(bar(74, "70", "120", "69"))
    assert [(event.trigger_id, event.to_state) for event in result.events] == [
        ("protected_swing_break", "CONFIRMED")
    ]


def test_configured_candidate_age_is_hashed_and_respected() -> None:
    default = _runtime()
    short = _runtime(max_age=2)
    assert default.detection_config_hash != short.detection_config_hash
    for item in chain_series():
        short.process_bar(item)
    assert short.process_bar(bar(15, "120", "121", "119")).instances[0].state == "CANDIDATE"
    result = short.process_bar(bar(16, "120", "121", "119"))
    assert [(event.trigger_id, event.to_state) for event in result.events] == [
        ("candidate_age_limit", "EXPIRED")
    ]


@pytest.mark.parametrize("invalid_maximum", [None, 0, -1, True, "60"])
def test_malformed_candidate_age_fails_closed_at_detector_boundary(
    invalid_maximum: object,
) -> None:
    runtime = _runtime()
    for item in chain_series():
        runtime.process_bar(item)
    assert runtime.instances[0].state == "CANDIDATE"
    frame = runtime.aggregator.update(bar(15, "120", "121", "119"))
    bar_input = DetectorInput(
        frame=frame,
        definition=REVERSAL_V1,
        parameters={"max_candidate_age_bars": invalid_maximum},
        config=runtime.config,
        detection_config_hash=runtime.detection_config_hash,
        run_id=runtime.run_id,
        dataset_revision_id=runtime.dataset_revision_id,
        instance=runtime.instances[0],
    )
    with pytest.raises(ValueError, match="max_candidate_age_bars must be a positive integer"):
        ReversalDetector().process_bar(bar_input)


def test_unqualified_and_misaligned_chain_does_not_open_candidate() -> None:
    runtime = _runtime()
    for item in chain_series()[:14]:
        runtime.process_bar(item)
    # The genuine chain is aligned/qualified only at its final cross. An
    # earlier opposing cross did not manufacture a candidate.
    assert not runtime.events

    frame = runtime.aggregator.update(chain_series()[14])
    components = dict(frame.components)
    components["swing_structure"] = {
        **components["swing_structure"],
        "overall_structure": OverallStructure.MIXED,
    }
    result = runtime.process_frame(replace(frame, components=components))
    assert result.instances[0].state == "INACTIVE"
    assert not result.events


def test_reset_replay_is_byte_identical() -> None:
    runtime = _runtime()
    series = (*chain_series(), bar(15, "70", "110", "69"))
    for item in series:
        runtime.process_bar(item)
    before = runtime.debug_json()
    runtime.reset()
    for item in series:
        runtime.process_bar(item)
    assert runtime.debug_json() == before
