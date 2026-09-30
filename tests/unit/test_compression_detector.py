"""SCRUM-84 executable Range Compression v1 lifecycle boundaries."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from test_detector_runtime import bar, config_with, pattern_selection

from market_analysis.config import ConfigParameter, PatternSelection
from market_analysis.detection import (
    COMPRESSION_V1,
    CompressionDetector,
    DetectorBinding,
    DetectorRuntime,
    DetectorRuntimeError,
    DetectorRuntimeResult,
)
from market_analysis.patterns import validate_event_evidence
from market_analysis.patterns.instance_context import encode_context


def _runtime(
    *, confirmation_bars: int = 5, detector: CompressionDetector | None = None
) -> DetectorRuntime:
    selection = (
        pattern_selection(COMPRESSION_V1)
        if confirmation_bars == 5
        else PatternSelection(
            pattern_id=COMPRESSION_V1.pattern_id,
            pattern_version=COMPRESSION_V1.pattern_version,
            parameters=(ConfigParameter(name="confirmation_bars", value=confirmation_bars),),
        )
    )
    return DetectorRuntime(
        config_with([selection]),
        [DetectorBinding(COMPRESSION_V1, detector or CompressionDetector(), reentrant=True)],
        run_id="run-84",
        dataset_revision_id="dataset-84",
    )


def _step(
    runtime: DetectorRuntime,
    index: int,
    compression: str | None,
    chop: str | None,
    *,
    available: bool = True,
    bandwidth: str = "0.05",
    with_session: bool = False,
) -> DetectorRuntimeResult:
    frame = runtime.aggregator.update(bar(index, "120", "121", "119"))
    components = dict(frame.components)
    state = dict(components["range_state"])
    state.update(
        compression_score=Decimal(compression) if compression is not None else None,
        choppiness_score=Decimal(chop) if chop is not None else None,
        bandwidth=Decimal(bandwidth),
        bandwidth_percentile=Decimal("0.2"),
        compression_regime="COMPRESSED" if compression is not None else "UNAVAILABLE",
        choppiness_regime="RANGE_LIKE" if chop is not None else "UNAVAILABLE",
    )
    components["range_state"] = state
    availability = {**frame.availability, "range_state": "AVAILABLE" if available else "WARMING_UP"}
    if with_session:
        components["session"] = {
            "session_name": "UK",
            "trading_date": "2026-01-05",
            "local_timestamp": datetime(2026, 1, 5, 12, tzinfo=UTC),
        }
        availability["session"] = "AVAILABLE"
    return runtime.process_frame(
        replace(frame, components=components, availability=availability, market_events_this_bar=())
    )


def test_five_consecutive_bars_confirm_with_earlier_candidate_event_time() -> None:
    runtime = _runtime()
    results = [_step(runtime, index, "80", "61.8001", with_session=True) for index in range(5)]
    assert [item.instances[0].state for item in results] == [
        "CANDIDATE",
        "CANDIDATE",
        "CANDIDATE",
        "CANDIDATE",
        "ACTIVE",
    ]
    assert not any(item.events for item in results[1:4])
    assert [(event.trigger_id, event.to_state) for event in runtime.events] == [
        ("compression_entry", "CANDIDATE"),
        ("persistence_confirmed", "ACTIVE"),
    ]
    confirmation = runtime.events[1]
    assert confirmation.event_time == bar(0).timestamp
    assert confirmation.detection_time == bar(4).timestamp
    assert not any(item.frame.market_events_this_bar for item in results)
    assert confirmation.rationale["items"][0]["features"]["bars_to_confirmation"]["value"] == 5
    features = runtime.events[0].rationale["items"][0]["features"]
    assert features["compression_entry_pass"]["value"] is True
    assert features["choppiness_entry_pass"]["value"] is True
    assert features["range_availability_status"]["value"] == "AVAILABLE"
    assert features["session_name"]["value"] == "UK"
    assert features["atr_atr"]["value"] == "2"
    for event in runtime.events:
        validate_event_evidence(COMPRESSION_V1, event.trigger_id, event.rationale)
    encode_context(COMPRESSION_V1, runtime.instances[0].context)


@pytest.mark.parametrize(
    ("compression", "chop", "available", "cause"),
    [
        ("79.999", "70", True, "COMPRESSION_ENTRY_FAILED"),
        ("90", "61.8", True, "CHOPPINESS_ENTRY_FAILED"),
        ("79", "60", True, "BOTH_ENTRY_AXES_FAILED"),
        (None, None, False, "RANGE_STATE_UNAVAILABLE"),
    ],
)
def test_candidate_fails_on_first_nonqualifying_or_unavailable_bar(
    compression: str | None, chop: str | None, available: bool, cause: str
) -> None:
    runtime = _runtime()
    _step(runtime, 0, "90", "70")
    interrupted = _step(runtime, 1, compression, chop, available=available)
    assert interrupted.instances[0].state == "INVALIDATED"
    assert interrupted.events[0].trigger_id == "candidate_interrupted"
    assert interrupted.instances[0].context["invalidated_cause"] == cause
    assert (
        interrupted.events[0].rationale["items"][0]["features"]["invalidated_cause"]["value"]
        == cause
    )
    later = _step(runtime, 2, "90", "70")
    assert later.instances[0].state == "CANDIDATE"
    assert later.instances[0].instance_id == f"{COMPRESSION_V1.pattern_id}:1"
    assert [item.state for item in runtime.occurrences] == ["INVALIDATED", "CANDIDATE"]


@pytest.mark.parametrize(
    ("compression", "chop", "cause"),
    [
        ("59.999", "70", "COMPRESSION_RELEASE"),
        ("70", "49.999", "CHOPPINESS_RELEASE"),
        ("59.999", "49.999", "BOTH"),
    ],
)
def test_active_hysteresis_and_strict_release_cause(
    compression: str, chop: str, cause: str
) -> None:
    runtime = _runtime()
    for index in range(5):
        _step(runtime, index, "90", "70")
    # Both scores have fallen below their entry thresholds but equal the
    # release thresholds, so the confirmed episode stays active.
    equality = _step(runtime, 5, "60", "50")
    assert equality.instances[0].state == "ACTIVE"
    assert not equality.events
    released = _step(runtime, 6, compression, chop)
    assert released.instances[0].state == "COMPLETED"
    assert released.events[0].trigger_id == "compression_released"
    assert released.instances[0].context["release_cause"] == cause
    features = released.events[0].rationale["items"][0]["features"]
    assert features["release_cause"]["value"] == cause
    assert features["active_duration_bars"]["value"] == 2
    assert features["active_duration_seconds"]["value"] == 120
    assert features["observed_bar_count"]["value"] == 6
    assert not released.frame.market_events_this_bar
    encode_context(COMPRESSION_V1, released.instances[0].context)


def test_unavailable_bar_does_not_invent_an_active_release() -> None:
    runtime = _runtime()
    for index in range(5):
        _step(runtime, index, "90", "70")
    unavailable = _step(runtime, 5, None, None, available=False)
    assert unavailable.instances[0].state == "ACTIVE"
    assert not unavailable.events
    assert _step(runtime, 6, "60", "50").instances[0].state == "ACTIVE"


def test_entry_thresholds_are_independent_and_strict_on_chop() -> None:
    runtime = _runtime()
    assert _step(runtime, 0, "80", "61.8").instances[0].state == "INACTIVE"
    assert _step(runtime, 1, "79.999", "80").instances[0].state == "INACTIVE"
    assert _step(runtime, 2, "90", "40").instances[0].state == "INACTIVE"
    assert _step(runtime, 3, "80", "61.8001").instances[0].state == "CANDIDATE"


def test_configured_persistence_count_changes_hash_and_confirmation_bar() -> None:
    assert _runtime().detection_config_hash != _runtime(confirmation_bars=3).detection_config_hash
    runtime = _runtime(confirmation_bars=3)
    assert _step(runtime, 0, "90", "70").instances[0].state == "CANDIDATE"
    assert _step(runtime, 1, "90", "70").instances[0].state == "CANDIDATE"
    assert _step(runtime, 2, "90", "70").instances[0].state == "ACTIVE"
    with pytest.raises(ValueError, match="confirmation_bars"):
        _runtime(confirmation_bars=1)


def test_reset_replay_is_identical_and_no_breakout_is_required() -> None:
    runtime = _runtime()
    observations = [("90", "70")] * 5 + [("60", "50"), ("59", "49")]
    for index, (compression, chop) in enumerate(observations):
        _step(runtime, index, compression, chop)
    before = runtime.debug_json()
    assert [event.trigger_id for event in runtime.events] == [
        "compression_entry",
        "persistence_confirmed",
        "compression_released",
    ]
    runtime.reset()
    for index, (compression, chop) in enumerate(observations):
        _step(runtime, index, compression, chop)
    assert runtime.debug_json() == before


def test_confirmation_cannot_cite_another_occurrences_candidate_time() -> None:
    first_candidate_time = bar(0).timestamp

    class CrossOccurrenceCitation(CompressionDetector):
        def _candidate(self, bar_input, observed):  # type: ignore[no-untyped-def]
            output = super()._candidate(bar_input, observed)
            if output.transitions and bar_input.instance.instance_id.endswith(":1"):
                return replace(
                    output,
                    transitions=(replace(output.transitions[0], event_time=first_candidate_time),),
                )
            return output

    runtime = _runtime(confirmation_bars=2, detector=CrossOccurrenceCitation())
    _step(runtime, 0, "90", "70")
    _step(runtime, 1, "79", "70")  # first occurrence invalidates
    _step(runtime, 2, "90", "70")  # new occurrence owns only bar 2
    before = runtime.debug_json()
    with pytest.raises(DetectorRuntimeError, match="same-occurrence detector event"):
        _step(runtime, 3, "90", "70")
    assert runtime.debug_json() == before
