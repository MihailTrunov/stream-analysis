"""Incremental completed-close EMA with an SMA seed at the configured period.

Before ``period`` closes the EMA value is unavailable (None). At the period-th
close it is the arithmetic mean of those closes. Thereafter
EMA_i = EMA_(i-1) + 2/(period+1) * (close_i - EMA_(i-1)).
No missing timestamp synthesizes a bar. Arithmetic is fixed at 34 significant
decimal digits, independent of the caller's Decimal context; reference tests
compare to 1e-12 absolute tolerance.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, localcontext

from market_analysis.config import DetectionAnalysisConfig
from market_analysis.domain import Bar

from .incremental import IncrementalMarketState, MarketStateError

EMA_PRECISION = 34


class EmaState(IncrementalMarketState):
    """One independently configured EMA selected in the immutable run config."""

    def __init__(self, run_config: DetectionAnalysisConfig, *, component_id: str = "ema") -> None:
        selection = next(
            (item for item in run_config.components if item.component_id == component_id), None
        )
        if selection is None or not selection.enabled or selection.component_version != "1":
            raise MarketStateError("enabled EMA v1 selection is required")
        parameters = {item.name: item.value for item in selection.parameters}
        if set(parameters) != {"period"}:
            raise MarketStateError("EMA v1 requires exactly one period parameter")
        period = parameters["period"]
        if isinstance(period, bool) or not isinstance(period, int) or period < 1:
            raise MarketStateError("EMA period must be a positive integer")
        self.component_id = component_id
        self.period = period
        with localcontext() as context:
            context.prec = EMA_PRECISION
            self.alpha = Decimal(2) / Decimal(period + 1)
        super().__init__(run_config, warmup_completed_bars=5 * period)

    def _reset_state(self) -> None:
        self._seed_count = 0
        self._seed_sum = Decimal(0)
        self._ema: Decimal | None = None

    def _update_completed_bar(self, bar: Bar) -> None:
        with localcontext() as context:
            context.prec = EMA_PRECISION
            if self._ema is None:
                self._seed_count += 1
                self._seed_sum += bar.close
                if self._seed_count == self.period:
                    self._ema = self._seed_sum / Decimal(self.period)
            else:
                self._ema += self.alpha * (bar.close - self._ema)

    def _state_values(self) -> Mapping[str, object]:
        return {
            "component_id": self.component_id,
            "period": self.period,
            "alpha": self.alpha,
            "seed_method": "sma_period_closes",
            "ema": self._ema,
            "value_ready": self._ema is not None,
        }
