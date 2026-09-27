from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from market_analysis.config import DetectionAnalysisConfig
from market_analysis.domain import Bar, Timeframe
from market_analysis.indicators import IncrementalMarketState, MarketStateError


class FakeComponent(IncrementalMarketState):
    """A counting component with no indicator or structural formula."""

    def _reset_state(self) -> None:
        self.closes: list[Decimal] = []

    def _update_completed_bar(self, bar: Bar) -> None:
        self.closes.append(bar.close)

    def _state_values(self) -> dict[str, object]:
        return {"observed": {"closes": self.closes}}


class EmaShapedComponent(IncrementalMarketState):
    """Contract fixture, not the SCRUM-69 EMA formula."""

    def _reset_state(self) -> None:
        self.value: Decimal | None = None

    def _update_completed_bar(self, bar: Bar) -> None:
        self.value = bar.close if self.value is None else (self.value + bar.close) / 2

    def _state_values(self) -> dict[str, object]:
        return {"value": self.value}


class AtrShapedComponent(IncrementalMarketState):
    """Contract fixture for a component retaining the prior observable close."""

    def _reset_state(self) -> None:
        self.prior_close: Decimal | None = None
        self.last_range: Decimal | None = None

    def _update_completed_bar(self, bar: Bar) -> None:
        prior = bar.open if self.prior_close is None else self.prior_close
        self.last_range = max(bar.high - bar.low, abs(bar.high - prior), abs(bar.low - prior))
        self.prior_close = bar.close

    def _state_values(self) -> dict[str, object]:
        return {"prior_close": self.prior_close, "last_range": self.last_range}


class StructuralShapedComponent(IncrementalMarketState):
    """Contract fixture for a stateful structural component without swing rules."""

    def _reset_state(self) -> None:
        self.prior_close: Decimal | None = None
        self.direction = "unknown"

    def _update_completed_bar(self, bar: Bar) -> None:
        if self.prior_close is not None:
            self.direction = "up" if bar.close > self.prior_close else "not-up"
        self.prior_close = bar.close

    def _state_values(self) -> dict[str, object]:
        return {"direction": self.direction, "prior_close": self.prior_close}


def config() -> DetectionAnalysisConfig:
    return DetectionAnalysisConfig(instrument_id="US30", timeframe=Timeframe.M1, calendar_id="cal")


def bar(minute: int, *, instrument: str = "US30", timeframe: Timeframe = Timeframe.M1,
        complete: bool = True) -> Bar:
    return Bar(
        instrument_id=instrument,
        timeframe=timeframe,
        timestamp=datetime(2026, 1, 2, 12, tzinfo=UTC) + timedelta(minutes=minute),
        open=Decimal("1"),
        high=Decimal("2"),
        low=Decimal("1"),
        close=Decimal("1.25") + Decimal(minute) / 100,
        is_complete=complete,
    )


def test_initialization_update_warmup_and_reset() -> None:
    component = FakeComponent(config(), warmup_completed_bars=2)
    assert component.warmup_completed_bars == 2
    assert component.state.completed_bars == 0
    assert component.state.last_timestamp is None
    assert component.state.is_warm is False

    component.update(bar(0))
    assert component.state.completed_bars == 1
    assert component.state.is_warm is False
    component.update(bar(1))
    assert component.state.completed_bars == 2
    assert component.state.last_timestamp == bar(1).timestamp
    assert component.state.is_warm is True
    assert component.state.values["observed"]["closes"] == (Decimal("1.25"), Decimal("1.26"))  # type: ignore[index]

    component.reset()
    assert component.state.completed_bars == 0
    assert component.state.last_timestamp is None
    assert component.state.values["observed"]["closes"] == ()  # type: ignore[index]


@pytest.mark.parametrize(
    ("rejected", "message"),
    [
        (bar(0), "strictly increasing"),
        (bar(-1), "strictly increasing"),
        (bar(2, instrument="DAX"), "instrument/timeframe"),
        (bar(2, timeframe=Timeframe.M5), "instrument/timeframe"),
        (bar(2, complete=False), "completed"),
        (object(), "canonical Bar"),
    ],
)
def test_invalid_input_is_rejected_without_mutating_state(rejected: Any, message: str) -> None:
    component = FakeComponent(config(), warmup_completed_bars=1)
    component.update(bar(0))
    before = component.debug_json()
    with pytest.raises(MarketStateError, match=message):
        component.update(rejected)
    assert component.debug_json() == before


def test_state_is_detached_recursively_read_only_and_config_is_frozen() -> None:
    component = FakeComponent(config(), warmup_completed_bars=1)
    component.update(bar(0))
    snapshot = component.state
    with pytest.raises(FrozenInstanceError):
        snapshot.completed_bars = 99  # type: ignore[misc]
    with pytest.raises(TypeError):
        snapshot.values["new"] = 1  # type: ignore[index]
    with pytest.raises(TypeError):
        snapshot.values["observed"]["closes"] = ()  # type: ignore[index]
    with pytest.raises(AttributeError):
        snapshot.values["observed"]["closes"].append(Decimal("9"))  # type: ignore[union-attr,index]
    component.closes.append(Decimal("9"))
    assert snapshot.values["observed"]["closes"] == (Decimal("1.25"),)  # type: ignore[index]
    with pytest.raises(ValidationError):
        component.run_config.instrument_id = "DAX"  # type: ignore[misc]


def test_replay_after_reset_matches_fresh_component_and_debug_json() -> None:
    bars = [bar(0), bar(1), bar(3)]
    component = FakeComponent(config(), warmup_completed_bars=2)
    for item in bars:
        component.update(item)
    first_state = component.state
    first_debug = component.debug_json()
    assert json.loads(first_debug)["values"]["observed"]["closes"] == ["1.25", "1.26", "1.28"]

    component.reset()
    fresh = FakeComponent(config(), warmup_completed_bars=2)
    for item in bars:
        component.update(item)
        fresh.update(item)
        assert component.state == fresh.state
        assert component.debug_json() == fresh.debug_json()
    assert component.state == first_state
    assert component.debug_json() == first_debug


@pytest.mark.parametrize(
    "component_type", (EmaShapedComponent, AtrShapedComponent, StructuralShapedComponent)
)
def test_indicator_and_structural_shapes_share_the_incremental_contract(
    component_type: type[IncrementalMarketState],
) -> None:
    bars = (bar(0), bar(1), bar(2))
    component = component_type(config(), warmup_completed_bars=2)
    for current in bars:
        component.update(current)
    expected = component.debug_json()
    assert component.state.is_warm
    assert component.state.completed_bars == len(bars)

    component.reset()
    assert component.state.completed_bars == 0
    for current in bars:
        component.update(current)
    assert component.debug_json() == expected


@pytest.mark.parametrize("requirement", [-1, True, 1.5])
def test_invalid_warmup_requirement_is_rejected(requirement: Any) -> None:
    with pytest.raises(MarketStateError, match="warmup_completed_bars"):
        FakeComponent(config(), warmup_completed_bars=requirement)
