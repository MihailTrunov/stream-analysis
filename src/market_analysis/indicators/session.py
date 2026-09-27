"""Incremental session state derived only from a pinned versioned calendar."""

from __future__ import annotations

from collections.abc import Mapping

from market_analysis.config import DetectionAnalysisConfig
from market_analysis.domain import Bar, SessionState, TradingCalendar

from .incremental import IncrementalMarketState, MarketStateError


class SessionComponent(IncrementalMarketState):
    """Produce local/session context without mutating the canonical UTC bar."""

    def __init__(
        self,
        run_config: DetectionAnalysisConfig,
        calendar: TradingCalendar,
        *,
        pinned_calendar_version: str,
    ) -> None:
        if (
            not isinstance(calendar, TradingCalendar)
            or calendar.calendar_id != run_config.calendar_id
            or calendar.instrument_id != run_config.instrument_id
            or calendar.version != pinned_calendar_version
        ):
            raise MarketStateError("session calendar identity/version differs from pinned run")
        self.calendar = calendar
        super().__init__(run_config, warmup_completed_bars=0)

    def _reset_state(self) -> None:
        self._session: SessionState | None = None

    def _update_completed_bar(self, bar: Bar) -> None:
        self._session = self.calendar.derive(bar.timestamp)

    @property
    def session_state(self) -> SessionState | None:
        return self._session

    def _state_values(self) -> Mapping[str, object]:
        state = self._session
        return {
            "calendar_id": self.calendar.calendar_id,
            "calendar_version": self.calendar.version,
            "timezone_name": self.calendar.timezone_name,
            "local_timestamp": None if state is None else state.local_timestamp,
            "trading_date": None if state is None else state.trading_date.isoformat(),
            "session_name": None if state is None else state.session_name,
            "elapsed_seconds": None if state is None or state.elapsed is None
            else int(state.elapsed.total_seconds()),
            "is_break": False if state is None else state.is_break,
            "is_holiday": False if state is None else state.is_holiday,
            "is_open": False if state is None else state.is_open,
        }
