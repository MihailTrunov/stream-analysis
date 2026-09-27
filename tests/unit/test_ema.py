from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest

from market_analysis.config import ComponentSelection, ConfigParameter, DetectionAnalysisConfig
from market_analysis.domain import Bar, Timeframe
from market_analysis.indicators import EmaState, MarketStateError

START = datetime(2026, 1, 2, 12, tzinfo=UTC)
TOLERANCE = Decimal("0.000000000001")


def config(*periods: tuple[str, int]) -> DetectionAnalysisConfig:
    return DetectionAnalysisConfig(
        instrument_id="US30", timeframe=Timeframe.M1, calendar_id="cal-v1",
        components=tuple(
            ComponentSelection(
                component_id="ema", component_version="1",
                instance_id=None if name == "ema" else name,
                parameters=(ConfigParameter(name="period", value=period),),
            )
            for name, period in periods
        ),
    )


def bars(closes: tuple[str, ...]) -> tuple[Bar, ...]:
    return tuple(
        Bar("US30", Timeframe.M1, START + timedelta(minutes=index),
            Decimal(close), Decimal(close), Decimal(close), Decimal(close))
        for index, close in enumerate(closes)
    )


def test_ema_reference_sma_seed_and_incremental_values() -> None:
    # Independent hand calculation: period 3, alpha 1/2; closes 10, 20, 30,
    # 40, 20 give None, None, 20, 30, 25.
    component = EmaState(config(("ema", 3)))
    observed: list[Decimal | None] = []
    for bar in bars(("10", "20", "30", "40", "20")):
        component.update(bar)
        value = component.state.values["ema"]
        assert value is None or isinstance(value, Decimal)
        observed.append(value)
    assert observed[:2] == [None, None]
    assert all(
        actual is not None and abs(actual - expected) <= TOLERANCE
        for actual, expected in zip(
            observed[2:], (Decimal("20"), Decimal("30"), Decimal("25")), strict=True
        )
    )
    assert component.warmup_completed_bars == 15
    assert not component.state.is_warm
    assert component.state.values["seed_method"] == "sma_period_closes"


def test_period_one_edge_values_and_reset_replay_parity() -> None:
    component = EmaState(config(("ema", 1)))
    series = bars(("0.00000001", "999999999.99999999", "0.00000001", "2", "3"))
    outputs = []
    for bar in series:
        component.update(bar)
        outputs.append(component.debug_json())
        assert component.state.values["ema"] == bar.close
    assert component.state.is_warm
    component.reset()
    assert component.state.values["ema"] is None
    assert component.state.completed_bars == 0
    for index, bar in enumerate(series):
        component.update(bar)
        assert component.debug_json() == outputs[index]


def test_ema_is_independent_of_callers_decimal_precision() -> None:
    run_config = config(("ema", 3))
    series = bars(("0.00000001", "1.23456789", "9.87654321", "3.33333333"))
    baseline = EmaState(run_config)
    constrained = EmaState(run_config)
    for bar in series:
        baseline.update(bar)
        with localcontext() as context:
            context.prec = 4
            constrained.update(bar)
    assert constrained.debug_json() == baseline.debug_json()


def test_two_configured_periods_do_not_share_state_or_synthesize_gaps() -> None:
    run_config = config(("ema", 2), ("ema_slow", 3))
    fast = EmaState(run_config)
    slow = EmaState(run_config, instance_id="ema_slow")
    series = bars(("1", "2", "3", "4"))
    for bar in (series[0], series[1], series[3]):
        fast.update(bar)
        slow.update(bar)
    assert fast.state.completed_bars == slow.state.completed_bars == 3
    assert abs(fast.state.values["ema"] - Decimal(19) / Decimal(6)) <= TOLERANCE
    assert abs(slow.state.values["ema"] - Decimal(7) / Decimal(3)) <= TOLERANCE
    assert fast.state.values["ema"] != slow.state.values["ema"]
    fast.reset()
    assert fast.state.values["ema"] is None
    assert abs(slow.state.values["ema"] - Decimal(7) / Decimal(3)) <= TOLERANCE


@pytest.mark.parametrize("period", [0, -1, True, "3"])
def test_invalid_period_is_rejected(period: object) -> None:
    with pytest.raises(MarketStateError, match="positive integer"):
        EmaState(config(("ema", period)))  # type: ignore[arg-type]
