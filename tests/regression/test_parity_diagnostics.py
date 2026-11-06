"""Mutation probes keep parity normalization from hiding analytical differences."""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest
from parity_support import Tolerance, Trace, assert_parity, first_divergence


@pytest.fixture
def trace():
    return Trace(
        invariants={
            "source_checksum": "source",
            "initialization_origin": "2026-01-05T10:00:00Z",
            "config_hash": "detection-config",
            "component_versions": {"atr": "1"},
            "calendar_version": "calendar-v1",
            "aggregation_version": "aggregation-v1",
        },
        checkpoints=[
            {
                "available_at": f"2026-01-05T10:0{index + 1}:00Z",
                "frame": {
                    "components": {"atr": {"value": "10.0000"}},
                    "market_events": [{"event_type": "EMA_CROSS", "source_instance_id": "ema"}],
                },
                "events": [
                    {
                        "state": "CANDIDATE",
                        "within_bar_ordinal": 0,
                        "rationale": {"source_market_event_refs": ["a" * 64]},
                    },
                    {"state": "CONFIRMED", "within_bar_ordinal": 1},
                ],
            }
            for index in range(3)
        ],
        occurrences=[{"context": {"schema": "occurrence-context-v1", "price": "12.5"}}],
        execution={
            "run_id": "run-one",
            "event_id": "event-one",
            "created_at": "earlier",
            "evaluation_plan_hash": "plan-one",
            "playback_speed": 1,
        },
    )


def test_only_operational_execution_metadata_is_excluded(trace):
    actual = replace(
        trace,
        execution={
            "run_id": "run-two",
            "event_id": "event-two",
            "created_at": "later",
            "evaluation_plan_hash": "plan-two",
            "playback_speed": 100,
        },
    )
    assert_parity(trace, actual)


@pytest.mark.parametrize(
    "key",
    [
        "source_checksum",
        "initialization_origin",
        "config_hash",
        "calendar_version",
        "aggregation_version",
        "component_versions",
    ],
)
def test_input_mismatch_is_not_hidden_by_matching_output(trace, key):
    actual = deepcopy(trace)
    actual.invariants[key] = "changed"
    difference = first_divergence(trace, actual)
    assert difference.path == ("invariants", key)
    assert difference.checkpoint == "input lineage"


def test_first_component_divergence_precedes_later_event_difference(trace):
    actual = deepcopy(trace)
    actual.checkpoints[0]["frame"]["components"]["atr"]["value"] = "11"
    actual.checkpoints[2]["events"][0]["state"] = "REJECTED"
    with pytest.raises(AssertionError, match=r"10:01:00Z.*components.atr.value.*10.0000.*11"):
        assert_parity(trace, actual)


def test_event_order_is_preserved(trace):
    actual = deepcopy(trace)
    actual.checkpoints[1]["events"].reverse()
    difference = first_divergence(trace, actual)
    assert difference.checkpoint == "2026-01-05T10:02:00Z"
    assert difference.path[:4] == ("checkpoints", "1", "events", "0")


def test_opaque_source_references_and_component_ids_are_semantic(trace):
    actual = deepcopy(trace)
    actual.checkpoints[0]["events"][0]["rationale"]["source_market_event_refs"][0] = "b" * 64
    assert first_divergence(trace, actual).path[-2:] == ("source_market_event_refs", "0")
    actual = deepcopy(trace)
    actual.checkpoints[0]["frame"]["market_events"][0]["source_instance_id"] = "different-ema"
    assert first_divergence(trace, actual).path[-1] == "source_instance_id"


def test_existing_occurrence_context_has_strict_schema_and_value_comparison(trace):
    actual = deepcopy(trace)
    actual.occurrences[0]["context"]["price"] = "12.6"
    assert first_divergence(trace, actual).path == ("occurrences", "0", "context", "price")
    actual = deepcopy(trace)
    actual.occurrences[0]["context"]["schema"] = "unknown-version"
    assert first_divergence(trace, actual).path[-1] == "schema"


def test_tolerance_is_fixture_defined_and_path_specific(trace):
    actual = deepcopy(trace)
    actual.checkpoints[1]["frame"]["components"]["atr"]["value"] = "10.0001"
    assert first_divergence(trace, actual) is not None
    tolerances = {
        ("checkpoints", "*", "frame", "components", "atr", "value"): Tolerance(
            absolute=Decimal("0.0001")
        )
    }
    assert_parity(trace, actual, tolerances=tolerances)
    actual.occurrences[0]["context"]["price"] = "12.5001"
    assert first_divergence(trace, actual, tolerances=tolerances).path[-1] == "price"


def test_numeric_relative_tolerance_does_not_accept_bool_or_nonfinite_values(trace):
    tolerances = {
        ("checkpoints", "*", "frame", "components", "atr", "value"): Tolerance(
            relative=Decimal("0.001")
        )
    }
    for value in (True, "NaN", "Infinity", "not-a-number"):
        actual = deepcopy(trace)
        actual.checkpoints[0]["frame"]["components"]["atr"]["value"] = value
        assert first_divergence(trace, actual, tolerances=tolerances) is not None


@pytest.mark.parametrize("value", [Decimal("-1"), Decimal("NaN"), Decimal("Infinity")])
def test_invalid_tolerance_is_rejected(value):
    with pytest.raises(ValueError, match="finite and nonnegative"):
        Tolerance(absolute=value)


def test_missing_checkpoint_event_and_field_report_the_actual_location(trace):
    actual = deepcopy(trace)
    actual.checkpoints.pop()
    assert first_divergence(trace, actual).path == ("checkpoints", "2")
    actual = deepcopy(trace)
    actual.checkpoints[0]["events"].pop()
    assert first_divergence(trace, actual).path == ("checkpoints", "0", "events", "1")
    actual = deepcopy(trace)
    del actual.checkpoints[0]["frame"]["components"]["atr"]["value"]
    assert first_divergence(trace, actual).path[-1] == "value"


def test_optional_numeric_availability_remains_exact_with_a_declared_tolerance(trace):
    left, right = deepcopy(trace), deepcopy(trace)
    left.checkpoints[0]["frame"]["components"]["atr"]["value"] = None
    right.checkpoints[0]["frame"]["components"]["atr"]["value"] = None
    tolerances = {
        ("checkpoints", "*", "frame", "components", "atr", "value"): Tolerance(
            absolute=Decimal("0.01")
        )
    }
    assert_parity(left, right, tolerances=tolerances)
    right.checkpoints[0]["frame"]["components"]["atr"]["value"] = "0"
    assert first_divergence(left, right, tolerances=tolerances) is not None


def test_identical_nonfinite_numeric_values_are_not_valid_parity(trace):
    for value in (float("inf"), float("nan"), Decimal("Infinity"), Decimal("NaN")):
        left, right = deepcopy(trace), deepcopy(trace)
        left.checkpoints[0]["frame"]["components"]["atr"]["value"] = value
        right.checkpoints[0]["frame"]["components"]["atr"]["value"] = value
        assert first_divergence(left, right) is not None
