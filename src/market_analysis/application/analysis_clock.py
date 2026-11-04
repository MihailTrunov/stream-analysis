"""Analytical clock advancement through verified scheduled closures only."""

from datetime import datetime

from market_analysis.domain import TradingCalendar
from market_analysis.domain.aggregation import MINUTE


def verify_closed_interval(calendar: TradingCalendar, start: datetime, end: datetime) -> None:
    if end.tzinfo is None or end < start:
        raise ValueError("analytical clock cannot move backwards or use a naive instant")
    minute = start
    while minute < end:
        if calendar.derive(minute).is_open:
            raise ValueError(f"missing expected-open source minute {minute.isoformat()}")
        minute += MINUTE
