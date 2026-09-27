"""Causal True Range and Wilder ATR over completed canonical bars.

First-bar True Range is high-low. Later bars use max(high-low,
abs(high-prior close), abs(low-prior close)). ATR is unavailable before
``period`` True Ranges; the seed is their arithmetic mean. Subsequent values
use Wilder smoothing: (previous ATR * (period-1) + current TR) / period.
Arithmetic is fixed at 34 significant Decimal digits, regardless of caller
context. No missing timestamp is filled with a synthetic bar.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, localcontext

from market_analysis.config import DetectionAnalysisConfig
from market_analysis.domain import Bar

from .incremental import IncrementalMarketState, MarketStateError

ATR_PRECISION = 34


@dataclass(frozen=True, slots=True)
class VolatilityState:
    """Observed absolute volatility only; no future-window rank or score."""

    period: int
    true_range: Decimal | None
    atr: Decimal | None

    @property
    def is_ready(self) -> bool:
        return self.atr is not None


class AtrState(IncrementalMarketState):
    """Current absolute volatility without future-normalized classification."""

    def __init__(self, run_config: DetectionAnalysisConfig, *, component_id: str = "atr") -> None:
        selection = next(
            (item for item in run_config.components if item.component_id == component_id), None
        )
        if selection is None or not selection.enabled or selection.component_version != "1":
            raise MarketStateError("enabled ATR v1 selection is required")
        parameters = {item.name: item.value for item in selection.parameters}
        if set(parameters) != {"period"}:
            raise MarketStateError("ATR v1 requires exactly one period parameter")
        period = parameters["period"]
        if isinstance(period, bool) or not isinstance(period, int) or period < 1:
            raise MarketStateError("ATR period must be a positive integer")
        self.component_id = component_id
        self.period = period
        super().__init__(run_config, warmup_completed_bars=5 * period + 1)

    def _reset_state(self) -> None:
        self._prior_close: Decimal | None = None
        self._true_range: Decimal | None = None
        self._seed_count = 0
        self._seed_sum = Decimal(0)
        self._atr: Decimal | None = None

    def _update_completed_bar(self, bar: Bar) -> None:
        with localcontext() as context:
            context.prec = ATR_PRECISION
            intrabar = bar.high - bar.low
            true_range = (
                intrabar if self._prior_close is None else max(
                    intrabar,
                    abs(bar.high - self._prior_close),
                    abs(bar.low - self._prior_close),
                )
            )
            if self._atr is None:
                self._seed_count += 1
                self._seed_sum += true_range
                if self._seed_count == self.period:
                    self._atr = self._seed_sum / Decimal(self.period)
            else:
                self._atr = (
                    self._atr * Decimal(self.period - 1) + true_range
                ) / Decimal(self.period)
            self._true_range = true_range
            self._prior_close = bar.close

    def _state_values(self) -> Mapping[str, object]:
        return {
            "component_id": self.component_id,
            "period": self.period,
            "smoothing": "wilder",
            "seed_method": "sma_period_true_ranges",
            "true_range": self._true_range,
            "atr": self._atr,
            "value_ready": self._atr is not None,
        }

    @property
    def volatility_state(self) -> VolatilityState:
        return VolatilityState(self.period, self._true_range, self._atr)
