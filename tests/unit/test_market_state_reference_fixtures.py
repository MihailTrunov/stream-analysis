"""SCRUM-111 checked-in, hand-reviewed reference values for market primitives.

The fixtures are inputs and authored expectations, never recordings written by
the implementation under test. A semantic change requires an intentional edit.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from market_analysis.config import ComponentSelection, ConfigParameter, DetectionAnalysisConfig
from market_analysis.domain import Bar, SessionWindow, Timeframe, TradingCalendar
from market_analysis.indicators import MarketStateAggregator

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "market_state"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _value(raw: Any) -> Any:
    if isinstance(raw, dict) and set(raw) == {"decimal"}:
        return Decimal(raw["decimal"])
    return raw


def _frames(case: dict[str, Any]) -> list[dict[str, Any]]:
    selections = tuple(
        ComponentSelection(
            component_id=item["id"],
            component_version=item["version"],
            parameters=tuple(
                ConfigParameter(name=name, value=_value(value))
                for name, value in item["parameters"].items()
            ),
        )
        for item in case["components"]
    )
    cfg = DetectionAnalysisConfig(
        instrument_id=case["instrument_id"],
        timeframe=Timeframe.M1,
        calendar_id="cal-v1",
        components=selections,
    )
    state = MarketStateAggregator(
        cfg, run_id=case["run_id"], dataset_revision_id=case["dataset_revision_id"]
    )
    start = datetime.fromisoformat(case["start_utc"].replace("Z", "+00:00"))
    frames = []
    bars = case.get("bars_close_high_low")
    if bars is None:
        repetition = case["repeated_bars_close_high_low"]
        bars = [repetition["pattern"][index % len(repetition["pattern"])]
                for index in range(repetition["count"])]
    for index, (close, high, low) in enumerate(bars):
        timestamp = start + timedelta(minutes=index)
        bar = Bar(
            case["instrument_id"], Timeframe.M1, timestamp,
            Decimal(close), Decimal(high), Decimal(low), Decimal(close),
        )
        frames.append(json.loads(state.update(bar).debug_json()))
    return frames


def _at(value: Any, path: str) -> Any:
    for key in path.split("."):
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


@pytest.mark.parametrize(
    "name", ["ema_v1.json", "atr_v1.json", "range_v1.json",
             "range_warmup_v1.json", "integrated_chain_v1.json"]
)
def test_authored_market_state_reference(name: str) -> None:
    case = _fixture(name)
    first = _frames(case)
    assert first == _frames(case), "fixture must be repeatable from a fresh component chain"
    expected_events_by_bar = case.get("expected_events_by_bar", [[] for _ in first])
    assert len(first) == len(expected_events_by_bar)
    for index, (frame, expected_events) in enumerate(
        zip(first, expected_events_by_bar, strict=True)
    ):
        assert frame["run_id"] == case["run_id"]
        assert frame["dataset_revision_id"] == case["dataset_revision_id"]
        assert frame["completed_bars"] == index + 1
        assert frame["component_versions"] == {
            item["id"]: item["version"] for item in case["components"]
        }
        if "expected_detection_config_hash" in case:
            assert frame["detection_config_hash"] == case["expected_detection_config_hash"]
        events = frame["market_events_this_bar"]
        assert [event["event_type"] for event in events] == expected_events, index
        assert [event["ordinal"] for event in events] == list(range(len(events))), index
        for event in events:
            assert event["detection_time"] == frame["bar"]["timestamp"], index
            assert event["event_time"] <= event["detection_time"], index
            assert event["source_instance_id"] in frame["components"], index
    for index, checks in case["expected_paths_by_bar"].items():
        frame = first[int(index)]
        for path, expected in checks.items():
            assert _at(frame, path) == expected, (name, index, path)


def test_integrated_chain_delayed_evidence_is_not_backfilled() -> None:
    frames = _frames(_fixture("integrated_chain_v1.json"))
    assert not frames[5]["market_events_this_bar"] or all(
        event["event_type"] != "SWING_POINT_CONFIRMED"
        for event in frames[5]["market_events_this_bar"]
    )
    event = frames[6]["market_events_this_bar"][0]
    assert event["event_type"] == "SWING_POINT_CONFIRMED"
    assert event["event_time"] == frames[5]["bar"]["timestamp"]
    assert event["detection_time"] == frames[6]["bar"]["timestamp"]


def test_authored_session_calendar_reference() -> None:
    case = _fixture("session_v1.json")
    spec = case["calendar"]
    calendar = TradingCalendar(
        calendar_id=spec["calendar_id"], version=spec["version"],
        provider=spec["provider"], account=spec["account"],
        instrument_id=spec["instrument_id"], timezone_name=spec["timezone_name"],
        trading_day_boundary=time.fromisoformat(spec["trading_day_boundary"]),
        windows=tuple(SessionWindow(name, time.fromisoformat(start), time.fromisoformat(end))
                      for name, start, end in spec["windows"]),
        breaks=tuple(SessionWindow(name, time.fromisoformat(start), time.fromisoformat(end))
                     for name, start, end in spec["breaks"]),
        holidays=frozenset(date.fromisoformat(value) for value in spec["holidays"]),
    )
    cfg = DetectionAnalysisConfig(
        instrument_id=spec["instrument_id"], timeframe=Timeframe.M1,
        calendar_id=spec["calendar_id"],
    )
    state = MarketStateAggregator(
        cfg, run_id="fixture-run-111", dataset_revision_id="fixture-dataset-111",
        calendar=calendar, pinned_calendar_version=spec["version"],
    )
    for expected in case["cases"]:
        timestamp = datetime.fromisoformat(expected["utc"].replace("Z", "+00:00"))
        bar = Bar(spec["instrument_id"], Timeframe.M1, timestamp,
                  Decimal(1), Decimal(1), Decimal(1), Decimal(1))
        frame = state.update(bar)
        session = frame.components["session"]
        assert frame.component_versions["session"] == spec["version"]
        assert frame.availability["session"] == "AVAILABLE"
        assert session["local_timestamp"].isoformat() == expected["local"]
        assert session["session_name"] == expected["session"]
        assert session["elapsed_seconds"] == expected["elapsed_seconds"]
        assert session["is_open"] == expected["is_open"]
        assert session["is_break"] == expected["is_break"]
        assert session["is_holiday"] == expected["is_holiday"]
