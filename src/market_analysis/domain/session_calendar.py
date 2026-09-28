"""Versioned, provider/account/instrument-specific intraday calendar data.

Window bounds are local wall times relative to the analytical trading date.
The start is inclusive and end exclusive. A time before the trading-day
boundary belongs to the following civil date. Durations use UTC instants, so
an IANA daylight-saving change does not fabricate or remove elapsed minutes.
Weekdays and inclusive coverage dates refer to that analytical start date,
not the civil date of a timestamp after midnight in an overnight session.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from types import MappingProxyType
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class SessionCalendarError(ValueError):
    """Calendar identity, mapping, or local boundary is invalid."""


@dataclass(frozen=True, slots=True)
class SessionWindow:
    name: str
    start: time
    end: time

    def __post_init__(self) -> None:
        if not self.name.strip() or self.start == self.end:
            raise SessionCalendarError("session window needs a name and nonzero span")
        if self.start.tzinfo is not None or self.end.tzinfo is not None:
            raise SessionCalendarError("session bounds must be local wall times")


@dataclass(frozen=True, slots=True)
class CalendarException:
    trading_date: date
    closed: bool = False
    windows: tuple[SessionWindow, ...] | None = None
    breaks: tuple[SessionWindow, ...] | None = None

    def __post_init__(self) -> None:
        if self.windows is not None:
            object.__setattr__(self, "windows", tuple(self.windows))
        if self.breaks is not None:
            object.__setattr__(self, "breaks", tuple(self.breaks))
        if self.closed and (self.windows is not None or self.breaks is not None):
            raise SessionCalendarError("closed exception cannot specify windows")
        for items in (self.windows, self.breaks):
            if items is not None and any(not isinstance(item, SessionWindow) for item in items):
                raise SessionCalendarError("exception windows must be SessionWindow values")


@dataclass(frozen=True, slots=True)
class SessionState:
    timestamp: datetime
    local_timestamp: datetime
    trading_date: date
    session_name: str | None
    elapsed: timedelta | None
    calendar_id: str
    calendar_version: str
    is_break: bool
    is_holiday: bool

    @property
    def is_open(self) -> bool:
        return self.session_name is not None and not self.is_break and not self.is_holiday


@dataclass(frozen=True, slots=True)
class TradingCalendar:
    calendar_id: str
    version: str
    provider: str
    account: str
    instrument_id: str
    timezone_name: str
    trading_day_boundary: time
    windows: tuple[SessionWindow, ...]
    breaks: tuple[SessionWindow, ...] = ()
    holidays: frozenset[date] = frozenset()
    exceptions: tuple[CalendarException, ...] = ()
    trading_weekdays: frozenset[int] = frozenset(range(7))
    coverage_start: date | None = None
    coverage_end: date | None = None
    _exception_index: Mapping[date, CalendarException] = field(
        init=False, repr=False, compare=False, hash=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "windows", tuple(self.windows))
        object.__setattr__(self, "breaks", tuple(self.breaks))
        object.__setattr__(self, "holidays", frozenset(self.holidays))
        object.__setattr__(self, "exceptions", tuple(self.exceptions))
        object.__setattr__(self, "trading_weekdays", frozenset(self.trading_weekdays))
        for name in ("calendar_id", "version", "provider", "account", "instrument_id"):
            if not getattr(self, name).strip():
                raise SessionCalendarError(f"{name} must be nonempty")
        if self.trading_day_boundary.tzinfo is not None:
            raise SessionCalendarError("trading-day boundary must be a local wall time")
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise SessionCalendarError("unknown IANA timezone") from exc
        if not self.windows:
            raise SessionCalendarError("at least one named session window is required")
        if any(not isinstance(item, SessionWindow) for item in self.windows + self.breaks):
            raise SessionCalendarError("calendar windows and breaks must be SessionWindow values")
        if any(not isinstance(item, date) for item in self.holidays):
            raise SessionCalendarError("calendar holidays must be dates")
        if any(not isinstance(item, CalendarException) for item in self.exceptions):
            raise SessionCalendarError("calendar exceptions must be CalendarException values")
        if len({item.name for item in self.windows}) != len(self.windows):
            raise SessionCalendarError("session window names must be unique")
        dates = [item.trading_date for item in self.exceptions]
        if len(set(dates)) != len(dates):
            raise SessionCalendarError("exception trading dates must be unique")
        object.__setattr__(self, "_exception_index", MappingProxyType({
            item.trading_date: item for item in self.exceptions
        }))
        if any(
            type(day) is not int or not 0 <= day <= 6 for day in self.trading_weekdays
        ):
            raise SessionCalendarError("trading weekdays must be integers from 0 (Monday) to 6")
        if (self.coverage_start is None) != (self.coverage_end is None):
            raise SessionCalendarError("calendar coverage requires both start and end dates")
        if self.coverage_start is not None and self.coverage_end is not None:
            if type(self.coverage_start) is not date or type(self.coverage_end) is not date:
                raise SessionCalendarError("calendar coverage bounds must be analytical dates")
            if self.coverage_end < self.coverage_start:
                raise SessionCalendarError("calendar coverage end must not precede start")

    def _instant(self, trading_date: date, wall_time: time) -> datetime:
        civil_date = (
            trading_date if wall_time >= self.trading_day_boundary
            else trading_date + timedelta(days=1)
        )
        local = datetime.combine(civil_date, wall_time, ZoneInfo(self.timezone_name))
        instant = local.astimezone(UTC)
        if instant.astimezone(local.tzinfo).replace(tzinfo=None) != local.replace(tzinfo=None):
            raise SessionCalendarError("calendar boundary falls in a nonexistent local time")
        return instant

    def _match(
        self, timestamp: datetime, trading_date: date, windows: tuple[SessionWindow, ...]
    ) -> tuple[SessionWindow, datetime] | None:
        match = None
        for window in windows:
            start = self._instant(trading_date, window.start)
            end_date = (
                trading_date + timedelta(days=1)
                if window.end == self.trading_day_boundary
                else trading_date
            )
            end = self._instant(end_date, window.end)
            if end <= start:
                raise SessionCalendarError("window must advance within a trading day")
            if start <= timestamp < end:
                if match is not None:
                    raise SessionCalendarError("overlapping session windows are ambiguous")
                match = (window, start)
        return match

    def derive(self, timestamp: datetime) -> SessionState:
        if (
            not isinstance(timestamp, datetime)
            or timestamp.tzinfo is None
            or timestamp.utcoffset() is None
        ):
            raise SessionCalendarError("bar timestamp must be timezone-aware")
        timestamp = timestamp.astimezone(UTC)
        local = timestamp.astimezone(ZoneInfo(self.timezone_name))
        trading_date = (
            local.date() if local.timetz().replace(tzinfo=None) >= self.trading_day_boundary
            else local.date() - timedelta(days=1)
        )
        if self.coverage_start is not None and self.coverage_end is not None:
            if not self.coverage_start <= trading_date <= self.coverage_end:
                raise SessionCalendarError("trading date is outside verified calendar coverage")
        exception = self._exception_index.get(trading_date)
        holiday = trading_date in self.holidays
        if exception is not None and exception.closed:
            holiday = True
        elif exception is not None:
            holiday = False  # explicit exception can reopen a listed holiday
        windows = (
            self.windows if exception is None or exception.windows is None else exception.windows
        )
        breaks = self.breaks if exception is None or exception.breaks is None else exception.breaks
        closed = holiday or (
            trading_date.weekday() not in self.trading_weekdays and exception is None
        )
        matched = None if closed else self._match(timestamp, trading_date, windows)
        break_match = None if closed else self._match(timestamp, trading_date, breaks)
        return SessionState(
            timestamp=timestamp, local_timestamp=local, trading_date=trading_date,
            session_name=None if matched is None or break_match is not None else matched[0].name,
            elapsed=None if matched is None or break_match is not None else timestamp - matched[1],
            calendar_id=self.calendar_id, calendar_version=self.version,
            is_break=break_match is not None, is_holiday=holiday,
        )


class CalendarRegistry:
    """No fallback calendar: every exact provider/account/instrument must be registered."""

    def __init__(self, calendars: tuple[TradingCalendar, ...]) -> None:
        self._calendars: dict[tuple[str, str, str], TradingCalendar] = {}
        for calendar in calendars:
            key = (calendar.provider, calendar.account, calendar.instrument_id)
            if key in self._calendars:
                raise SessionCalendarError("duplicate provider/account/instrument calendar")
            self._calendars[key] = calendar

    def resolve(self, provider: str, account: str, instrument_id: str) -> TradingCalendar:
        calendar = self._calendars.get((provider, account, instrument_id))
        if calendar is None:
            raise SessionCalendarError("unknown provider/account/instrument calendar mapping")
        return calendar
