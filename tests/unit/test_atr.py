from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest

from market_analysis.config import ComponentSelection, ConfigParameter, DetectionAnalysisConfig
from market_analysis.domain import Bar, Timeframe
from market_analysis.indicators import AtrState, MarketStateError

START = datetime(2026, 1, 2, 12, tzinfo=UTC)
TOLERANCE = Decimal("0.000000000001")


def config(period: int) -> DetectionAnalysisConfig:
    return DetectionAnalysisConfig(
        instrument_id="US30", timeframe=Timeframe.M1, calendar_id="cal-v1",
        components=(ComponentSelection(
            component_id="atr", component_version="1",
            parameters=(ConfigParameter(name="period", value=period),),
        ),),
    )


def bar(index: int, high: str, low: str, close: str) -> Bar:
    return Bar(
        "US30", Timeframe.M1, START + timedelta(minutes=index),
        Decimal(close), Decimal(high), Decimal(low), Decimal(close),
    )


def test_wilder_atr_reference_with_gap_and_no_gap() -> None:
    # Independent hand fixture: TR = 2, 6, 3, 2. For period 2,
    # ATR = None, (2+6)/2=4, (4+3)/2=3.5, (3.5+2)/2=2.75.
    series = (
        bar(0, "11", "9", "10"),
        bar(1, "16", "14", "15"),  # gap above prior close 10: TR 6
        bar(2, "18", "15", "17"),
        bar(3, "18", "16", "17"),
    )
    component = AtrState(config(2))
    observed_tr = []
    observed_atr = []
    snapshots = []
    for item in series:
        component.update(item)
        observed_tr.append(component.state.values["true_range"])
        observed_atr.append(component.state.values["atr"])
        snapshots.append(component.debug_json())
    assert observed_tr == [Decimal(2), Decimal(6), Decimal(3), Decimal(2)]
    assert observed_atr[0] is None
    assert component.volatility_state.is_ready
    assert component.volatility_state.atr == Decimal("2.75")
    assert all(
        actual is not None and abs(actual - expected) <= TOLERANCE
        for actual, expected in zip(
            observed_atr[1:], (Decimal(4), Decimal("3.5"), Decimal("2.75")), strict=True
        )
    )
    assert component.warmup_completed_bars == 11
    assert component.state.is_warm is False
    component.reset()
    assert component.state.values["atr"] is None
    assert not component.volatility_state.is_ready
    for index, item in enumerate(series):
        component.update(item)
        assert component.debug_json() == snapshots[index]


def test_down_gap_period_one_and_missing_timestamp_not_synthesized() -> None:
    component = AtrState(config(1))
    component.update(bar(0, "101", "99", "100"))
    assert component.state.values["atr"] == Decimal(2)
    component.update(bar(10, "92", "90", "91"))
    assert component.state.values["true_range"] == Decimal(10)
    assert component.state.values["atr"] == Decimal(10)
    assert component.state.completed_bars == 2


def test_atr_is_independent_of_callers_decimal_precision() -> None:
    run_config = config(3)
    series = (
        bar(0, "1.234567891234", "0.987654321987", "1.123456789123"),
        bar(1, "1.999999999999", "1.111111111111", "1.500000000001"),
        bar(2, "2.111111111111", "1.222222222222", "1.777777777777"),
        bar(3, "2.333333333333", "1.333333333333", "1.888888888888"),
    )
    baseline, constrained = AtrState(run_config), AtrState(run_config)
    for item in series:
        baseline.update(item)
        with localcontext() as context:
            context.prec = 4
            constrained.update(item)
    assert baseline.debug_json() == constrained.debug_json()


@pytest.mark.parametrize("period", [0, -1, True, "3"])
def test_invalid_atr_period_is_rejected(period: object) -> None:
    with pytest.raises(MarketStateError, match="positive integer"):
        AtrState(config(period))  # type: ignore[arg-type]
