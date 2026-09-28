"""Offline UK/live OANDA M1 schedules verified on 2026-09-28.

These finite profiles retain the source interval start (the minute preceding
the published opening minute), rather than trimming canonical M1 bars.
Coverage is inclusive in analytical session-start dates. Holiday overrides
are dated evidence, never extrapolated recurrence rules. Building a calendar
binds an already verified account context; it does not authenticate an account.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from types import MappingProxyType
from zoneinfo import ZoneInfo

from market_analysis.domain import (
    CalendarException,
    SessionCalendarError,
    SessionWindow,
    TradingCalendar,
)

CALENDAR_VERSION = "oanda-uk-live-m1-2026-09-28-v1"
COVERAGE_START = date(2023, 9, 27)
COVERAGE_END = date(2026, 9, 28)


@dataclass(frozen=True, slots=True)
class OandaUkCalendarProfile:
    provider_symbol: str
    instrument_id: str
    display_name: str
    timezone_name: str
    trading_day_boundary: time
    windows: tuple[SessionWindow, ...]
    breaks: tuple[SessionWindow, ...]
    trading_weekdays: frozenset[int]
    exceptions: tuple[CalendarException, ...]
    version: str = CALENDAR_VERSION
    coverage_start: date = COVERAGE_START
    coverage_end: date = COVERAGE_END

    def __post_init__(self) -> None:
        object.__setattr__(self, "windows", tuple(self.windows))
        object.__setattr__(self, "breaks", tuple(self.breaks))
        object.__setattr__(self, "trading_weekdays", frozenset(self.trading_weekdays))
        object.__setattr__(self, "exceptions", tuple(self.exceptions))

    @property
    def calendar_id(self) -> str:
        return f"oanda-uk-live-{self.provider_symbol}"


def _dates(values: str) -> tuple[date, ...]:
    return tuple(date.fromisoformat(value) for value in values.split())


def _us_exceptions() -> tuple[CalendarException, ...]:
    # The table lists civil END dates; US analytical dates are the prior day.
    early_closes = (
        (time(12), """
            2023-11-23
            2024-01-15 2024-02-19 2024-05-27 2024-06-19 2024-07-04 2024-09-02 2024-11-28
            2025-01-20 2025-02-17 2025-05-26 2025-06-19 2025-07-04 2025-09-01 2025-11-27
            2026-01-19 2026-02-16 2026-05-25 2026-06-19 2026-07-03 2026-09-07
        """),
        (time(12, 15), """
            2023-11-24 2024-07-03 2024-11-29 2024-12-24
            2025-07-03 2025-11-28 2025-12-24
        """),
        (time(8, 15), "2026-04-03"),
    )
    exceptions = {
        civil_date - timedelta(days=1): CalendarException(
            civil_date - timedelta(days=1),
            windows=(SessionWindow("provider", time(17), close),),
            breaks=(SessionWindow("scheduled_break", close, time(17)),),
        )
        for close, values in early_closes for civil_date in _dates(values)
    }
    for trading_date in _dates("""
        2023-12-24 2023-12-31 2024-03-28 2024-12-24 2024-12-31
        2025-04-17 2025-12-24 2025-12-31
    """):
        exceptions[trading_date] = CalendarException(trading_date, closed=True)
    return tuple(exceptions[day] for day in sorted(exceptions))


def _de_exceptions() -> tuple[CalendarException, ...]:
    zone = ZoneInfo("Europe/Berlin")
    exceptions = {}
    day = COVERAGE_START
    while day <= COVERAGE_END:
        # Finite dated overrides: IANA DST at local noon selects the verified
        # summer opening; the opening remains 00:15 UTC in both seasons.
        if day.weekday() < 5 and datetime.combine(day, time(12), zone).dst():
            exceptions[day] = CalendarException(
                day,
                windows=(SessionWindow("provider", time(2, 15), time(22)),),
                breaks=(
                    SessionWindow("morning_break", time(0), time(2, 15)),
                    SessionWindow("evening_break", time(22), time(0)),
                ),
            )
        day += timedelta(days=1)
    for trading_date in _dates("""
        2023-12-25 2023-12-26
        2024-01-01 2024-03-29 2024-04-01 2024-05-01 2024-12-24 2024-12-25 2024-12-26 2024-12-31
        2025-01-01 2025-04-18 2025-04-21 2025-05-01 2025-12-24 2025-12-25 2025-12-26 2025-12-31
        2026-01-01 2026-04-03 2026-04-06 2026-05-01
    """):
        exceptions[trading_date] = CalendarException(trading_date, closed=True)
    return tuple(exceptions[day] for day in sorted(exceptions))


OANDA_UK_LIVE_PROFILES: Mapping[str, OandaUkCalendarProfile] = MappingProxyType({
    "US30_USD": OandaUkCalendarProfile(
        provider_symbol="US30_USD", instrument_id="US30", display_name="US Wall St30",
        timezone_name="America/Chicago", trading_day_boundary=time(17),
        windows=(SessionWindow("provider", time(17), time(16)),),
        breaks=(SessionWindow("scheduled_break", time(16), time(17)),),
        trading_weekdays=frozenset({6, 0, 1, 2, 3}), exceptions=_us_exceptions(),
    ),
    "DE30_EUR": OandaUkCalendarProfile(
        provider_symbol="DE30_EUR", instrument_id="DAX", display_name="Germany30",
        timezone_name="Europe/Berlin", trading_day_boundary=time(0),
        windows=(SessionWindow("provider", time(1, 15), time(22)),),
        breaks=(
            SessionWindow("morning_break", time(0), time(1, 15)),
            SessionWindow("evening_break", time(22), time(0)),
        ),
        trading_weekdays=frozenset(range(5)), exceptions=_de_exceptions(),
    ),
})


def build_oanda_uk_calendar(
    *, provider: str, region: str, environment: str, account: str, provider_symbol: str,
) -> TradingCalendar:
    """Bind an exact supported context without guessing aliases or account region."""
    if (provider, region, environment) != ("oanda", "UK", "live"):
        raise SessionCalendarError("unsupported provider/region/environment calendar mapping")
    profile = OANDA_UK_LIVE_PROFILES.get(provider_symbol)
    if profile is None:
        raise SessionCalendarError("unsupported exact OANDA instrument calendar mapping")
    if not isinstance(account, str) or not account.strip():
        raise SessionCalendarError("calendar binding requires a nonempty verified account")
    return TradingCalendar(
        calendar_id=profile.calendar_id, version=profile.version,
        provider=provider, account=account, instrument_id=profile.instrument_id,
        timezone_name=profile.timezone_name, trading_day_boundary=profile.trading_day_boundary,
        windows=profile.windows, breaks=profile.breaks, trading_weekdays=profile.trading_weekdays,
        exceptions=profile.exceptions,
        coverage_start=profile.coverage_start, coverage_end=profile.coverage_end,
    )
