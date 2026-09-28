from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest

from market_analysis.config import DetectionAnalysisConfig
from market_analysis.config.oanda_uk_calendars import (
    CALENDAR_VERSION,
    COVERAGE_END,
    COVERAGE_START,
    OANDA_UK_LIVE_PROFILES,
    build_oanda_uk_calendar,
)
from market_analysis.domain import (
    Bar,
    CalendarRegistry,
    SessionCalendarError,
    Timeframe,
    TradingCalendar,
)
from market_analysis.indicators import SessionComponent


def calendar(symbol: str = "US30_USD", **overrides: str) -> TradingCalendar:
    context = dict(provider="oanda", region="UK", environment="live", account="fixture-account",
                   provider_symbol=symbol)
    context.update(overrides)
    return build_oanda_uk_calendar(**context)


def stamp(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


@pytest.mark.parametrize("symbol,opening,closing", [
    ("US30_USD", "2024-01-07T23:00", "2024-01-08T22:00"),
    ("US30_USD", "2024-07-07T22:00", "2024-07-08T21:00"),
    # US/European DST mismatch weeks must follow each instrument's own zone.
    ("US30_USD", "2024-03-10T22:00", "2024-03-11T21:00"),
    ("US30_USD", "2024-10-27T22:00", "2024-10-28T21:00"),
    ("DE30_EUR", "2024-01-08T00:15", "2024-01-08T21:00"),
    ("DE30_EUR", "2024-07-08T00:15", "2024-07-08T20:00"),
    ("DE30_EUR", "2024-03-11T00:15", "2024-03-11T21:00"),
    ("DE30_EUR", "2024-10-28T00:15", "2024-10-28T21:00"),
    ("DE30_EUR", "2023-10-02T00:15", "2023-10-02T20:00"),
    ("DE30_EUR", "2025-03-31T00:15", "2025-03-31T20:00"),
    ("DE30_EUR", "2026-03-30T00:15", "2026-03-30T20:00"),
    ("DE30_EUR", "2025-10-27T00:15", "2025-10-27T21:00"),
    ("US30_USD", "2026-03-08T22:00", "2026-03-09T21:00"),
])
def test_verified_m1_session_floors_and_seasonal_utc_bounds(
    symbol: str, opening: str, closing: str,
) -> None:
    configured = calendar(symbol)
    start, end = stamp(opening), stamp(closing)
    before = configured.derive(start - timedelta(minutes=1))
    opened = configured.derive(start)
    last = configured.derive(end - timedelta(minutes=1))
    closed = configured.derive(end)
    assert not before.is_open
    assert opened.is_open and opened.session_name == "provider" and opened.elapsed == timedelta(0)
    assert last.is_open and last.elapsed == end - start - timedelta(minutes=1)
    assert not closed.is_open and closed.is_break
    assert opened.timestamp == start
    if symbol == "US30_USD":
        assert opened.trading_date == last.trading_date == start.date()
    else:
        assert opened.local_timestamp.time() in (time(1, 15), time(2, 15))
        assert last.local_timestamp.time() == time(21, 59)


def test_weekend_closures_sunday_us_reopen_and_german_evening_break() -> None:
    us, de = calendar(), calendar("DE30_EUR")
    assert us.derive(stamp("2024-01-12T21:59")).is_open
    for value in ("2024-01-12T23:00", "2024-01-13T12:00", "2024-01-14T22:59"):
        state = us.derive(stamp(value))
        assert not state.is_open and not state.is_holiday and not state.is_break
    assert us.derive(stamp("2024-01-14T23:00")).is_open
    for value in ("2024-01-13T00:15", "2024-01-14T00:15"):
        assert not de.derive(stamp(value)).is_open
    assert de.derive(stamp("2024-01-08T21:00")).is_break
    assert de.derive(stamp("2024-01-08T22:59")).is_break
    assert de.derive(stamp("2024-01-08T23:00")).is_break  # next local day's morning break


@pytest.mark.parametrize("civil_end,closing", [
    ("2023-11-23", "2023-11-23T18:00"),
    ("2024-01-15", "2024-01-15T18:00"),
    ("2024-07-03", "2024-07-03T17:15"),
    ("2024-12-24", "2024-12-24T18:15"),
    ("2025-11-28", "2025-11-28T18:15"),
    ("2026-07-03", "2026-07-03T17:00"),
    ("2026-04-03", "2026-04-03T13:15"),
])
def test_dated_us_early_closes_use_previous_analytical_date(
    civil_end: str, closing: str,
) -> None:
    configured, end = calendar(), stamp(closing)
    last = configured.derive(end - timedelta(minutes=1))
    closed = configured.derive(end)
    assert last.is_open
    assert last.trading_date == date.fromisoformat(civil_end) - timedelta(days=1)
    assert not closed.is_open and closed.is_break and not closed.is_holiday
    assert configured.derive(end + timedelta(minutes=30)).is_break


@pytest.mark.parametrize("symbol,timestamp", [
    ("US30_USD", "2023-12-25T12:00"),
    ("US30_USD", "2024-03-29T12:00"),
    ("US30_USD", "2025-04-18T12:00"),
    ("US30_USD", "2026-01-01T12:00"),
    ("DE30_EUR", "2023-12-26T12:00"),
    ("DE30_EUR", "2024-04-01T12:00"),
    ("DE30_EUR", "2025-12-24T12:00"),
    ("DE30_EUR", "2026-04-06T12:00"),
])
def test_dated_full_holidays_are_closed(symbol: str, timestamp: str) -> None:
    state = calendar(symbol).derive(stamp(timestamp))
    assert state.is_holiday and not state.is_open and not state.is_break


def test_holidays_are_instrument_specific_and_unexplained_gaps_remain_expected_open() -> None:
    us, de = calendar(), calendar("DE30_EUR")
    assert us.derive(stamp("2025-07-04T18:00")).is_break
    assert de.derive(stamp("2025-07-04T18:00")).is_open
    assert de.derive(stamp("2025-10-03T12:00")).is_open
    assert us.derive(stamp("2025-11-28T03:00")).is_open
    assert de.derive(stamp("2026-04-07T00:15")).is_open


@pytest.mark.parametrize("context", [
    {"provider": "other"}, {"region": "US"}, {"environment": "practice"},
    {"provider_symbol": "US30"}, {"provider_symbol": "DAX"},
    {"provider_symbol": "US30_USD "}, {"account": ""}, {"account": " "},
])
def test_factory_rejects_unsupported_exact_context(context: dict[str, str]) -> None:
    with pytest.raises(SessionCalendarError):
        calendar(**context)


def test_account_registry_profiles_and_coverage_are_pinned_and_immutable() -> None:
    us, de = calendar(), calendar("DE30_EUR")
    registry = CalendarRegistry((us, de))
    assert registry.resolve("oanda", "fixture-account", "US30") is us
    assert registry.resolve("oanda", "fixture-account", "DAX") is de
    with pytest.raises(SessionCalendarError, match="unknown"):
        registry.resolve("oanda", "other-account", "US30")
    with pytest.raises(SessionCalendarError, match="unknown"):
        registry.resolve("oanda", "fixture-account", "US30_USD")
    for configured in (us, de):
        assert configured.coverage_start == COVERAGE_START
        assert configured.coverage_end == COVERAGE_END
        assert configured.version == CALENDAR_VERSION
        for value in ("2023-09-26T12:00", "2026-09-30T12:00"):
            with pytest.raises(SessionCalendarError, match="coverage"):
                configured.derive(stamp(value))
    assert OANDA_UK_LIVE_PROFILES["US30_USD"].display_name == "US Wall St30"
    assert OANDA_UK_LIVE_PROFILES["DE30_EUR"].display_name == "Germany30"
    with pytest.raises(TypeError):
        OANDA_UK_LIVE_PROFILES["new"] = OANDA_UK_LIVE_PROFILES["US30_USD"]  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        OANDA_UK_LIVE_PROFILES["US30_USD"].version = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        us._exception_index[date(2024, 1, 1)] = us.exceptions[0]  # type: ignore[index]


def test_provider_coverage_includes_whole_first_and_last_analytical_sessions() -> None:
    us, de = calendar(), calendar("DE30_EUR")
    assert us.derive(stamp("2023-09-27T22:00")).is_open
    assert us.derive(stamp("2026-09-29T20:59")).is_open
    assert de.derive(stamp("2023-09-27T00:15")).is_open
    assert de.derive(stamp("2026-09-28T19:59")).is_open
    with pytest.raises(SessionCalendarError, match="coverage"):
        us.derive(stamp("2023-09-27T21:59"))
    with pytest.raises(SessionCalendarError, match="coverage"):
        us.derive(stamp("2026-09-29T22:00"))


def test_provider_component_is_deterministic_across_dst_and_reset() -> None:
    configured = calendar("DE30_EUR")
    config = DetectionAnalysisConfig(
        instrument_id="DAX", timeframe=Timeframe.M1, calendar_id=configured.calendar_id
    )
    component = SessionComponent(config, configured, pinned_calendar_version=CALENDAR_VERSION)
    outputs = []
    for value in ("2024-03-28T00:15", "2024-04-02T00:15"):
        bar = Bar("DAX", Timeframe.M1, stamp(value), *(Decimal(1),) * 4)
        component.update(bar)
        assert component.session_state is not None and component.session_state.is_open
        outputs.append((bar, component.debug_json()))
    component.reset()
    for bar, expected in outputs:
        component.update(bar)
        assert component.debug_json() == expected
