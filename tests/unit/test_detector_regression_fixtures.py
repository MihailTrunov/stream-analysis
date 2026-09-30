"""SCRUM-86 authored cross-detector event envelopes over canonical bars.

These literals are reviewed expectations, not snapshots generated at test
time. Detector-specific tests retain the compact threshold/precedence cases.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest
from test_detector_runtime import bar, config_with, pattern_selection

from market_analysis.config import ComponentSelection, ConfigParameter, DetectionAnalysisConfig
from market_analysis.detection import (
    COMPRESSION_V1,
    CONTINUATION_V1,
    REVERSAL_V1,
    CompressionDetector,
    ContinuationDetector,
    DetectorBinding,
    DetectorRuntime,
    ReversalDetector,
)
from market_analysis.domain import Timeframe
from market_analysis.indicators import market_event_semantic_ref
from market_analysis.patterns.instance_context import decode_context, encode_context

# Explicit close/high/low inputs; open equals close. The first five flat bars
# seed the component chain. Entries 5-14 are the hand-reviewed SCRUM-77 chain.
TREND_BARS = (
    ("100", "101", "99"),
    ("100", "101", "99"),
    ("100", "101", "99"),
    ("100", "101", "99"),
    ("100", "101", "99"),
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

ENTRY_MARKET_EVENTS = (
    "SWING_POINT_CONFIRMED",
    "SWING_STRUCTURE_CLASSIFIED",
    "EMA_CROSS",
    "TREND_LEG_STARTED",
    "TREND_LEG_QUALIFIED",
)

# All prefix current-bar events, including empty steps, are reviewed. The
# mirrored chain has one extra early protected-high break on bar 7.
TREND_MARKET_EVENTS_UP = {
    5: ("EMA_CROSS",),
    6: ("SWING_POINT_CONFIRMED", "SWING_STRUCTURE_CLASSIFIED", "EMA_CROSS"),
    8: ("SWING_POINT_CONFIRMED", "SWING_STRUCTURE_CLASSIFIED", "EMA_CROSS"),
    10: ("SWING_POINT_CONFIRMED", "SWING_STRUCTURE_CLASSIFIED"),
    11: ("EMA_CROSS",),
    12: ("SWING_POINT_CONFIRMED", "SWING_STRUCTURE_CLASSIFIED", "EMA_CROSS"),
    13: ("SWING_HIGH_CLOSE_BREAK",),
}
TREND_MARKET_EVENTS_DOWN = {
    **TREND_MARKET_EVENTS_UP,
    7: ("SWING_POINT_CONFIRMED", "SWING_STRUCTURE_CLASSIFIED", "SWING_HIGH_CLOSE_BREAK"),
    13: ("SWING_LOW_CLOSE_BREAK",),
}


def _event(
    sequence: int,
    trigger: str,
    from_state: str,
    to_state: str,
    source_refs: tuple[str, ...],
    features: dict[str, object],
) -> dict[str, object]:
    return {
        "sequence": sequence,
        "trigger": trigger,
        "from": from_state,
        "to": to_state,
        "source_refs": source_refs,
        "features": features,
    }


# Each case gives the exact expected current-bar market events and detector
# transitions at its two decisive bars. All preceding steps must have no
# detector events and remain INACTIVE. Hashes and refs are literal lineage
# expectations; changing them requires a deliberate fixture/version review.
CASES: tuple[dict[str, Any], ...] = (
    {
        "name": "reversal-up",
        "definition": REVERSAL_V1,
        "detector": ReversalDetector,
        "mirror": False,
        "finish": ("70", "110", "69"),
        "config_hash": "b20cc194d940f2e34af0f07486f298697c20fe60b41267389e82a934f05f3246",
        "entry_context": {
            "source_direction": "UP",
            "candidate_direction": "DOWN",
            "protected_swing_index": 4,
            "source_structure": "BULLISH",
        },
        "frozen_context": {
            "source_leg_ref": "f1d6ee4522d8d8560eed4fdd8eab5756475fb91f7cb44669d7c78945838ca91b",
            "candidate_cross_ref": (
                "ca4a7f3e5a725b2163d78ced6a4f923e8c25b6c488228d3b21945bbd5c1ab3c1"
            ),
            "protected_swing_price": "89",
        },
        "exit_context": {"source_direction": "UP", "candidate_direction": "DOWN"},
        "exit_market_events": (
            "SWING_LOW_CLOSE_BREAK",
            "TREND_LEG_ENDED",
            "TREND_LEG_QUALIFICATION_ENDED",
        ),
        "entry": _event(
            0,
            "opposing_ema_cross",
            "INACTIVE",
            "CANDIDATE",
            ("ca4a7f3e5a725b2163d78ced6a4f923e8c25b6c488228d3b21945bbd5c1ab3c1",),
            {"candidate_direction": "PENDING"},
        ),
        "exit": (
            _event(
                1,
                "protected_swing_break",
                "CANDIDATE",
                "CONFIRMED",
                (
                    "02cc66e938cf7fa74d052664bc74b93df331e93921b526d147ef23e1015c6bc7",
                    "cee7c1836c43103582b91e61e77c02ee10a6234707718ab1e83341a09c472724",
                ),
                {
                    "candidate_direction": "DOWN",
                    "bars_since_candidate": 1,
                    "protected_swing_price_at_break": "89",
                },
            ),
        ),
    },
    {
        "name": "reversal-down",
        "definition": REVERSAL_V1,
        "detector": ReversalDetector,
        "mirror": True,
        "finish": ("70", "110", "69"),
        "config_hash": "b20cc194d940f2e34af0f07486f298697c20fe60b41267389e82a934f05f3246",
        "entry_context": {
            "source_direction": "DOWN",
            "candidate_direction": "UP",
            "protected_swing_index": 5,
            "source_structure": "BEARISH",
        },
        "frozen_context": {
            "source_leg_ref": "c888b03e54e9b5efaa6cd374b0a1945a2473ceca73a0cc06de30bb85853a0069",
            "candidate_cross_ref": (
                "ad90e46486797f9c846106a56c272d84bfcdc61e95f9fc97f95740daf75852a8"
            ),
            "protected_swing_price": "111",
        },
        "exit_context": {"source_direction": "DOWN", "candidate_direction": "UP"},
        "exit_market_events": (
            "SWING_HIGH_CLOSE_BREAK",
            "TREND_LEG_ENDED",
            "TREND_LEG_QUALIFICATION_ENDED",
        ),
        "entry": _event(
            0,
            "opposing_ema_cross",
            "INACTIVE",
            "CANDIDATE",
            ("ad90e46486797f9c846106a56c272d84bfcdc61e95f9fc97f95740daf75852a8",),
            {"candidate_direction": "PENDING"},
        ),
        "exit": (
            _event(
                1,
                "protected_swing_break",
                "CANDIDATE",
                "CONFIRMED",
                (
                    "17eeafb3a5ef7fe858ce240d6019db9b0bb737ce8379a3e78838721a7e0fc7ab",
                    "efc8ca90675814a654b7fe68b75ae18b7f5273a776a86addde27beefd777e0c6",
                ),
                {
                    "candidate_direction": "UP",
                    "bars_since_candidate": 1,
                    "protected_swing_price_at_break": "111",
                },
            ),
        ),
    },
    {
        "name": "continuation-up",
        "definition": CONTINUATION_V1,
        "detector": ContinuationDetector,
        "mirror": False,
        "finish": ("150", "160", "119"),
        "config_hash": "9dd9e098fce78d069e780289ac0605b12b09b67a1bdf602824d70e81e624c70a",
        "entry_context": {
            "source_direction": "UP",
            "continuation_swing_index": 5,
            "protected_swing_index": 4,
            "max_pullback_depth_points": "22",
        },
        "frozen_context": {
            "source_leg_ref": "45550909b58b5f7d1c0995eecaa0be607ae1c335791ba24978ea2baad3030d43",
            "candidate_cross_ref": (
                "fea6e4482915af481d9be7a92e6942d2ee16026f7a0f8b423f2b3d1c3888ce5b"
            ),
            "continuation_swing_ref": (
                "3a68ba3a253ddfa6cfab410611eeed540f4162d6698ef1a538b3db27440e52a5"
            ),
            "protected_swing_ref": (
                "e4c88c09653d43534999ac5dd5a5849114618a681805d010c45de81cf2aaf40e"
            ),
        },
        "exit_context": {
            "source_direction": "UP",
            "continuation_swing_index": 5,
            "protected_swing_index": 4,
            "reaction_swing_label": "HL",
        },
        "exit_market_events": (
            "SWING_POINT_CONFIRMED",
            "SWING_STRUCTURE_CLASSIFIED",
            "SWING_HIGH_CLOSE_BREAK",
            "EMA_CROSS",
        ),
        "entry": _event(
            0,
            "opposing_ema_cross",
            "INACTIVE",
            "CANDIDATE",
            ("fea6e4482915af481d9be7a92e6942d2ee16026f7a0f8b423f2b3d1c3888ce5b",),
            {"source_direction": "UP", "bars_since_candidate": 0},
        ),
        "exit": (
            _event(
                1,
                "ema_reclaim",
                "CANDIDATE",
                "RECLAIMED",
                ("9898730c9f0e169681155eaac1c07a18d67c15ee19025e0de7dc6bda6b73a8ef",),
                {
                    "source_direction": "UP",
                    "reaction_swing_label": "HL",
                    "max_pullback_depth_before_reclaim": "22",
                },
            ),
            _event(
                2,
                "continuation_break",
                "RECLAIMED",
                "CONFIRMED",
                ("aa15bcabb2250864a2551a17e89cc9fb6a0b0a760482946b0c377ac16dc0b7a4",),
                {
                    "source_direction": "UP",
                    "reaction_swing_label": "HL",
                    "bars_candidate_to_confirmation": 1,
                    "bars_reclaim_to_confirmation": 0,
                },
            ),
        ),
    },
    {
        "name": "continuation-down",
        "definition": CONTINUATION_V1,
        "detector": ContinuationDetector,
        "mirror": True,
        "finish": ("150", "160", "119"),
        "config_hash": "9dd9e098fce78d069e780289ac0605b12b09b67a1bdf602824d70e81e624c70a",
        "entry_context": {
            "source_direction": "DOWN",
            "continuation_swing_index": 6,
            "protected_swing_index": 5,
            "max_pullback_depth_points": "22",
        },
        "frozen_context": {
            "source_leg_ref": "e95ddd18481c6bf217b4932652c73b06a513ce01e4085105122199923cebf94f",
            "candidate_cross_ref": (
                "16a3131a5aed2622bd486d75a6aac4e5845a24b34f6edc67a273a6cd689b183c"
            ),
            "continuation_swing_ref": (
                "39a85182077adc674a434cbdb32c95742062fb718ad93082a0b02e437249ce70"
            ),
            "protected_swing_ref": (
                "a65a6ac26244116521ace036aa7e6f40d42f06f967bdd6cf23ca0f3a171aed1f"
            ),
        },
        "exit_context": {
            "source_direction": "DOWN",
            "continuation_swing_index": 6,
            "protected_swing_index": 5,
            "reaction_swing_label": "LH",
        },
        "exit_market_events": (
            "SWING_POINT_CONFIRMED",
            "SWING_STRUCTURE_CLASSIFIED",
            "SWING_LOW_CLOSE_BREAK",
            "EMA_CROSS",
        ),
        "entry": _event(
            0,
            "opposing_ema_cross",
            "INACTIVE",
            "CANDIDATE",
            ("16a3131a5aed2622bd486d75a6aac4e5845a24b34f6edc67a273a6cd689b183c",),
            {"source_direction": "DOWN", "bars_since_candidate": 0},
        ),
        "exit": (
            _event(
                1,
                "ema_reclaim",
                "CANDIDATE",
                "RECLAIMED",
                ("d736aff6b12c71756d4f7dd38b8afe463de6a7e8b96d0300757f9bd015b4c154",),
                {
                    "source_direction": "DOWN",
                    "reaction_swing_label": "LH",
                    "max_pullback_depth_before_reclaim": "22",
                },
            ),
            _event(
                2,
                "continuation_break",
                "RECLAIMED",
                "CONFIRMED",
                ("447081d8f93f14261c674f8d588b9d20647dca7858d8d469f7d4e768165e007a",),
                {
                    "source_direction": "DOWN",
                    "reaction_swing_label": "LH",
                    "bars_candidate_to_confirmation": 1,
                    "bars_reclaim_to_confirmation": 0,
                },
            ),
        ),
    },
)


def _series(case: dict[str, Any]) -> tuple[Any, ...]:
    triples = (*TREND_BARS, case["finish"])
    bars = tuple(bar(i, *triple) for i, triple in enumerate(triples))
    if not case["mirror"]:
        return bars
    return tuple(
        replace(
            item,
            open=Decimal(200) - item.open,
            high=Decimal(200) - item.low,
            low=Decimal(200) - item.high,
            close=Decimal(200) - item.close,
        )
        for item in bars
    )


def _runtime(case: dict[str, Any]) -> DetectorRuntime:
    definition = case["definition"]
    return DetectorRuntime(
        config_with([pattern_selection(definition)]),
        [DetectorBinding(definition, case["detector"](), reentrant=True)],
        run_id="fixture",
        dataset_revision_id="fixture",
    )


def _assert_event(
    case: dict[str, Any],
    actual: Any,
    expected: dict[str, object],
    index: int,
    market_refs: set[str],
) -> None:
    label = f"{case['name']}/bar-{index}/event-{expected['sequence']}"
    assert actual.pattern_id == case["definition"].pattern_id, label
    assert actual.pattern_version == "1", label
    assert actual.instance_id == case["definition"].pattern_id, label
    assert actual.detection_config_hash == case["config_hash"], label
    assert actual.run_id == actual.dataset_revision_id == "fixture", label
    assert (actual.sequence, actual.trigger_id, actual.from_state, actual.to_state) == (
        expected["sequence"],
        expected["trigger"],
        expected["from"],
        expected["to"],
    ), label
    assert actual.event_time == actual.detection_time == bar(index).timestamp, label
    assert actual.rationale["source_market_event_refs"] == expected["source_refs"], label
    assert set(actual.rationale["source_market_event_refs"]) <= market_refs, label
    features = actual.rationale["items"][0]["features"]
    for key, value in expected["features"].items():
        assert features[key]["value"] == value, f"{label}/rationale/{key}"


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_authored_canonical_event_envelope_and_replay(case: dict[str, Any]) -> None:
    series = _series(case)
    runtime = _runtime(case)
    assert runtime.detection_config_hash == case["config_hash"], case["name"]
    assert runtime.config.patterns[0].pattern_version == "1", case["name"]
    assert (
        next(
            parameter.value
            for parameter in runtime.config.patterns[0].parameters
            if parameter.name == "max_candidate_age_bars"
        )
        == 60
    )

    for index, item in enumerate(series):
        result = runtime.process_bar(item)
        label = f"{case['name']}/bar-{index}"
        expected_market = (
            ENTRY_MARKET_EVENTS
            if index == 14
            else case["exit_market_events"]
            if index == 15
            else (TREND_MARKET_EVENTS_DOWN if case["mirror"] else TREND_MARKET_EVENTS_UP).get(
                index, ()
            )
        )
        assert tuple(event.event_type.value for event in result.frame.market_events_this_bar) == (
            expected_market
        ), f"{label}/current-bar-market-events"
        if index < 14:
            assert result.instances[0].state == "INACTIVE", label
            assert not result.events, label
            continue
        expected_events = (case["entry"],) if index == 14 else case["exit"]
        assert result.instances[0].state == ("CANDIDATE" if index == 14 else "CONFIRMED"), label
        assert result.instances[0].instance_id == case["definition"].pattern_id, label
        for key, value in (case["entry_context"] if index == 14 else case["exit_context"]).items():
            actual = result.instances[0].context[key]
            assert (str(actual) if isinstance(actual, Decimal) else actual) == value, (
                f"{label}/context/{key}"
            )
        for key, value in case["frozen_context"].items():
            actual = result.instances[0].context[key]
            assert (str(actual) if isinstance(actual, Decimal) else actual) == value, (
                f"{label}/frozen-context/{key}"
            )
        encoded = encode_context(case["definition"], result.instances[0].context)
        assert decode_context(case["definition"], encoded) == result.instances[0].context, label
        assert len(result.events) == len(expected_events), label
        market_refs = {
            market_event_semantic_ref(result.frame, event)
            for event in result.frame.market_events_this_bar
        }
        for actual, expected in zip(result.events, expected_events, strict=True):
            _assert_event(case, actual, expected, index, market_refs)

    assert len(runtime.occurrences) == 1, case["name"]
    baseline = runtime.debug_json()
    runtime.reset()
    for item in series:
        runtime.process_bar(item)
    assert runtime.debug_json() == baseline, f"{case['name']}/reset"

    fresh = _runtime(case)
    for item in series:
        fresh.process_bar(item)
    assert fresh.debug_json() == baseline, f"{case['name']}/fresh-runtime"

    framed = _runtime(case)
    for item in series:
        framed.process_frame(framed.aggregator.update(item))
    assert framed.debug_json() == baseline, f"{case['name']}/finalized-frame"


# These inputs are intentionally explicit rather than a score sequence. The
# first eight bars warm up ATR/RangeState; bars 8-12 keep identical nonzero
# four-close bandwidth while three-bar CHOP is 100. Bar 13 expands sharply.
COMPRESSION_BARS = (
    ("10", "12", "9"),
    ("11", "12", "9"),
    ("10", "12", "9"),
    ("11", "12", "9"),
    ("10", "12", "9"),
    ("11", "12", "9"),
    ("10", "12", "9"),
    ("11", "12", "9"),
    ("10", "12", "9"),
    ("11", "12", "9"),
    ("10", "12", "9"),
    ("11", "12", "9"),
    ("10", "12", "9"),
    ("20", "21", "9"),
)

COMPRESSION_EXPECTED = {
    8: {
        "state": "CANDIDATE",
        "qualifying_count": 1,
        "event": (0, "compression_entry", "INACTIVE", "CANDIDATE", 8, 8),
        "features": {
            "compression_score": "100",
            "choppiness_score": "100",
            "compression_entry_pass": True,
            "choppiness_entry_pass": True,
        },
    },
    9: {"state": "CANDIDATE", "qualifying_count": 2},
    10: {"state": "CANDIDATE", "qualifying_count": 3},
    11: {"state": "CANDIDATE", "qualifying_count": 4},
    12: {
        "state": "ACTIVE",
        "qualifying_count": 5,
        "event": (1, "persistence_confirmed", "CANDIDATE", "ACTIVE", 8, 12),
        "features": {
            "compression_score": "100",
            "choppiness_score": "100",
            "bars_to_confirmation": 5,
        },
    },
    13: {
        "state": "COMPLETED",
        "qualifying_count": 5,
        "event": (2, "compression_released", "ACTIVE", "COMPLETED", 13, 13),
        "features": {"compression_score": "0", "release_cause": "BOTH", "active_duration_bars": 1},
    },
}


def _compression_runtime() -> DetectorRuntime:
    def parameter(name: str, value: int) -> ConfigParameter:
        return ConfigParameter(name=name, value=value)

    config = DetectionAnalysisConfig(
        instrument_id="US30",
        timeframe=Timeframe.M1,
        calendar_id="cal-v1",
        components=(
            ComponentSelection(
                component_id="atr",
                component_version="1",
                parameters=(parameter("period", 2),),
            ),
            ComponentSelection(
                component_id="range_state",
                component_version="1",
                parameters=(
                    parameter("chop_period", 3),
                    parameter("bandwidth_period", 4),
                    parameter("compression_reference_bars", 6),
                ),
            ),
        ),
        patterns=(pattern_selection(COMPRESSION_V1),),
    )
    return DetectorRuntime(
        config,
        [DetectorBinding(COMPRESSION_V1, CompressionDetector(), reentrant=True)],
        run_id="fixture",
        dataset_revision_id="fixture",
    )


def test_authored_computed_compression_fixture_and_replay() -> None:
    series = tuple(bar(i, *triple) for i, triple in enumerate(COMPRESSION_BARS))
    runtime = _compression_runtime()
    assert runtime.detection_config_hash == (
        "c0c9cb7f2e0541042648ac5883db8ab59825a40ceae3055e8b983b4380ca04f9"
    )
    for index, item in enumerate(series):
        result = runtime.process_bar(item)
        label = f"compression/bar-{index}"
        assert not result.frame.market_events_this_bar, label
        expected = COMPRESSION_EXPECTED.get(index)
        if expected is None:
            assert result.frame.availability["range_state"] == "WARMING_UP", label
            assert result.instances[0].state == "INACTIVE", label
            assert not result.events, label
            continue
        assert result.frame.availability["range_state"] == "AVAILABLE", label
        assert result.instances[0].state == expected["state"], label
        assert result.instances[0].context["qualifying_count"] == expected["qualifying_count"], (
            label
        )
        assert result.instances[0].instance_id == COMPRESSION_V1.pattern_id, label
        encoded = encode_context(COMPRESSION_V1, result.instances[0].context)
        assert decode_context(COMPRESSION_V1, encoded) == result.instances[0].context, label
        expected_event = expected.get("event")
        if expected_event is None:
            assert not result.events, label
            continue
        assert len(result.events) == 1, label
        event = result.events[0]
        sequence, trigger, before, after, event_index, detection_index = expected_event
        assert (event.sequence, event.trigger_id, event.from_state, event.to_state) == (
            sequence,
            trigger,
            before,
            after,
        ), label
        assert event.event_time == series[event_index].timestamp, label
        assert event.detection_time == series[detection_index].timestamp, label
        assert event.pattern_id == COMPRESSION_V1.pattern_id, label
        assert event.pattern_version == "1", label
        assert event.detection_config_hash == runtime.detection_config_hash, label
        assert event.rationale["source_market_event_refs"] == (), label
        features = event.rationale["items"][0]["features"]
        for key, value in expected["features"].items():
            assert features[key]["value"] == value, f"{label}/rationale/{key}"

    assert len(runtime.occurrences) == 1
    baseline = runtime.debug_json()
    runtime.reset()
    for item in series:
        runtime.process_bar(item)
    assert runtime.debug_json() == baseline

    fresh = _compression_runtime()
    for item in series:
        fresh.process_frame(fresh.aggregator.update(item))
    assert fresh.debug_json() == baseline


def test_competing_reversal_and_continuation_candidates_share_canonical_cross() -> None:
    runtime = DetectorRuntime(
        config_with([pattern_selection(REVERSAL_V1), pattern_selection(CONTINUATION_V1)]),
        [
            DetectorBinding(REVERSAL_V1, ReversalDetector(), reentrant=True),
            DetectorBinding(CONTINUATION_V1, ContinuationDetector(), reentrant=True),
        ],
        run_id="fixture",
        dataset_revision_id="fixture",
    )
    assert runtime.detection_config_hash == (
        "4410be3859c46f0d87731f0a2b89dc9e17c43783feb33700798138d4121a16be"
    )
    series = tuple(bar(i, *triple) for i, triple in enumerate((*TREND_BARS, ("150", "160", "119"))))
    expected = {
        14: (
            (
                CONTINUATION_V1.pattern_id,
                0,
                "opposing_ema_cross",
                "CANDIDATE",
                "71e548eccddb6e8120bb557cd15a88f92300916a0e3dd47891a156f51c07f071",
            ),
            (
                REVERSAL_V1.pattern_id,
                0,
                "opposing_ema_cross",
                "CANDIDATE",
                "71e548eccddb6e8120bb557cd15a88f92300916a0e3dd47891a156f51c07f071",
            ),
        ),
        15: (
            (
                CONTINUATION_V1.pattern_id,
                1,
                "ema_reclaim",
                "RECLAIMED",
                "b78efe0cc0f76eff1f68c5d0b648f54a5e8b6b41f3a92a9985e90b94bd1f0e58",
            ),
            (
                CONTINUATION_V1.pattern_id,
                2,
                "continuation_break",
                "CONFIRMED",
                "ae309968c114d2c54fc6968b5900eda5bcf13d3a3922dcb37a571e6f53764cb1",
            ),
        ),
    }
    for index, item in enumerate(series):
        result = runtime.process_bar(item)
        assert [
            (
                event.pattern_id,
                event.sequence,
                event.trigger_id,
                event.to_state,
                event.rationale["source_market_event_refs"][0],
            )
            for event in result.events
        ] == list(expected.get(index, ())), f"competing/bar-{index}"
        if index == 15:
            assert [(instance.pattern_id, instance.state) for instance in result.instances] == [
                (CONTINUATION_V1.pattern_id, "CONFIRMED"),
                (REVERSAL_V1.pattern_id, "CANDIDATE"),
            ]
