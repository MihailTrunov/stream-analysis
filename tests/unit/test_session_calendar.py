from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest

from market_analysis.config import DetectionAnalysisConfig
from market_analysis.domain import (
    Bar,
    CalendarException,
    CalendarRegistry,
    SessionCalendarError,
    SessionWindow,
    Timeframe,
    TradingCalendar,
)
from market_analysis.indicators import SessionComponent


def calendar(**overrides: object) -> TradingCalendar:
    values = {
        "calendar_id": "research-us30",
        "version": "fixture-v1",
        "provider": "seed",
        "account": "local",
        "instrument_id": "US30",
        "timezone_name": "Europe/London",
        "trading_day_boundary": time(0),
        "windows": (
            SessionWindow("london", time(8), time(11)),
            SessionWindow("pre_us", time(11), time(14)),
            SessionWindow("us_open", time(14), time(17)),
            SessionWindow("late", time(17), time(20)),
        ),
    }
    values.update(overrides)
    return TradingCalendar(**values)  # type: ignore[arg-type]


def test_london_winter_summer_and_exact_session_boundaries() -> None:
    configured = calendar()
    winter = configured.derive(datetime(2026, 1, 5, 8, tzinfo=UTC))
    summer = configured.derive(datetime(2026, 7, 6, 7, tzinfo=UTC))
    assert winter.session_name == summer.session_name == "london"
    assert winter.local_timestamp.hour == summer.local_timestamp.hour == 8
    assert winter.elapsed == summer.elapsed == timedelta(0)
    before = configured.derive(datetime(2026, 1, 5, 7, 59, tzinfo=UTC))
    assert not before.is_open and before.session_name is None
    boundary = configured.derive(datetime(2026, 1, 5, 11, tzinfo=UTC))
    assert boundary.session_name == "pre_us" and boundary.elapsed == timedelta(0)
    end = configured.derive(datetime(2026, 1, 5, 20, tzinfo=UTC))
    assert not end.is_open
    assert configured.derive(datetime(2026, 7, 6, 7, tzinfo=UTC)) == summer


def test_trading_day_boundary_break_holiday_and_exception() -> None:
    configured = calendar(
        trading_day_boundary=time(17),
        breaks=(SessionWindow("pause", time(9), time(9, 15)),),
        holidays=frozenset({date(2026, 1, 5)}),
        exceptions=(CalendarException(date(2026, 1, 5), windows=(
            SessionWindow("special", time(8), time(10)),
        )),),
    )
    before = configured.derive(datetime(2026, 1, 6, 16, 59, tzinfo=UTC))
    after = configured.derive(datetime(2026, 1, 6, 17, tzinfo=UTC))
    assert before.trading_date == date(2026, 1, 5)
    assert after.trading_date == date(2026, 1, 6)
    opened = configured.derive(datetime(2026, 1, 6, 8, 30, tzinfo=UTC))
    assert opened.session_name == "special" and opened.is_open
    pause = configured.derive(datetime(2026, 1, 6, 9, tzinfo=UTC))
    assert pause.is_break and not pause.is_open and pause.session_name is None
    normal_holiday = calendar(holidays=frozenset({date(2026, 1, 5)}))
    assert normal_holiday.derive(datetime(2026, 1, 5, 8, tzinfo=UTC)).is_holiday
    closed = calendar(exceptions=(CalendarException(date(2026, 1, 5), closed=True),))
    assert closed.derive(datetime(2026, 1, 5, 8, tzinfo=UTC)).is_holiday


def test_registry_rejects_unknown_provider_account_or_instrument() -> None:
    registry = CalendarRegistry((calendar(),))
    assert registry.resolve("seed", "local", "US30").version == "fixture-v1"
    for key in (("oanda", "local", "US30"), ("seed", "other", "US30"),
                ("seed", "local", "DAX")):
        with pytest.raises(SessionCalendarError, match="unknown"):
            registry.resolve(*key)


def test_component_is_deterministic_and_resettable() -> None:
    configured = calendar()
    run_config = DetectionAnalysisConfig(
        instrument_id="US30", timeframe=Timeframe.M1, calendar_id=configured.calendar_id
    )
    component = SessionComponent(
        run_config, configured, pinned_calendar_version="fixture-v1"
    )
    series = (
        Bar("US30", Timeframe.M1, datetime(2026, 1, 5, 8, tzinfo=UTC),
            Decimal(1), Decimal(1), Decimal(1), Decimal(1)),
        Bar("US30", Timeframe.M1, datetime(2026, 1, 5, 8, 1, tzinfo=UTC),
            Decimal(1), Decimal(1), Decimal(1), Decimal(1)),
    )
    assert component.session_state is None
    outputs = []
    for item in series:
        component.update(item)
        outputs.append(component.debug_json())
    assert component.session_state is not None
    assert component.session_state.elapsed == timedelta(minutes=1)
    assert component.state.values["calendar_version"] == "fixture-v1"
    component.reset()
    assert component.session_state is None
    for index, item in enumerate(series):
        component.update(item)
        assert component.debug_json() == outputs[index]
    with pytest.raises(ValueError, match="identity/version"):
        SessionComponent(run_config, configured, pinned_calendar_version="stale-v0")


def test_invalid_calendar_and_nonexistent_dst_window_are_rejected() -> None:
    with pytest.raises(SessionCalendarError, match="IANA"):
        calendar(timezone_name="Not/A_Zone")
    with pytest.raises(SessionCalendarError, match="duplicate"):
        CalendarRegistry((calendar(), calendar()))
    transition = calendar(windows=(SessionWindow("transition", time(1, 30), time(3)),))
    with pytest.raises(SessionCalendarError, match="nonexistent"):
        transition.derive(datetime(2026, 3, 29, 2, tzinfo=UTC))


def test_weekdays_use_overnight_session_start_date_and_exact_boundaries() -> None:
    configured = calendar(
        timezone_name="America/New_York",
        trading_day_boundary=time(18),
        windows=(SessionWindow("provider", time(18), time(17)),),
        trading_weekdays=frozenset({0, 1, 2, 3, 6}),
    )
    # Sunday evening and Monday morning belong to the Sunday-started session.
    before = configured.derive(datetime(2026, 1, 4, 22, 59, tzinfo=UTC))
    opened = configured.derive(datetime(2026, 1, 4, 23, tzinfo=UTC))
    morning = configured.derive(datetime(2026, 1, 5, 13, tzinfo=UTC))
    end = configured.derive(datetime(2026, 1, 5, 22, tzinfo=UTC))
    assert not before.is_open and not before.is_holiday
    assert opened.is_open and opened.elapsed == timedelta(0)
    assert opened.trading_date == morning.trading_date == date(2026, 1, 4)
    assert morning.is_open and morning.elapsed == timedelta(hours=14)
    assert not end.is_open
    friday = configured.derive(datetime(2026, 1, 9, 13, tzinfo=UTC))
    weekend = configured.derive(datetime(2026, 1, 9, 23, tzinfo=UTC))
    assert friday.is_open and friday.trading_date == date(2026, 1, 8)
    assert not weekend.is_open and not weekend.is_holiday and not weekend.is_break


def test_exception_can_reopen_weekend_but_closed_exception_stays_closed() -> None:
    saturday, sunday = date(2026, 1, 3), date(2026, 1, 4)
    configured = calendar(
        trading_weekdays=frozenset(range(5)),
        breaks=(SessionWindow("pause", time(9), time(9, 15)),),
        exceptions=(
            CalendarException(saturday, windows=(SessionWindow("special", time(8), time(10)),)),
            CalendarException(sunday, closed=True),
        ),
    )
    opening = configured.derive(datetime(2026, 1, 3, 8, tzinfo=UTC))
    pause = configured.derive(datetime(2026, 1, 3, 9, tzinfo=UTC))
    closed = configured.derive(datetime(2026, 1, 4, 8, tzinfo=UTC))
    assert opening.is_open and opening.session_name == "special"
    assert pause.is_break and not pause.is_open
    assert closed.is_holiday and not closed.is_open
    assert not configured.derive(datetime(2026, 1, 3, 10, tzinfo=UTC)).is_open


def test_coverage_is_inclusive_and_uses_analytical_start_date() -> None:
    configured = calendar(
        trading_day_boundary=time(18),
        windows=(SessionWindow("provider", time(18), time(17)),),
        coverage_start=date(2026, 1, 5),
        coverage_end=date(2026, 1, 6),
    )
    first = configured.derive(datetime(2026, 1, 5, 18, tzinfo=UTC))
    last = configured.derive(datetime(2026, 1, 7, 16, 59, tzinfo=UTC))
    assert first.is_open and first.trading_date == date(2026, 1, 5)
    assert last.is_open and last.trading_date == date(2026, 1, 6)
    for timestamp in (
        datetime(2026, 1, 5, 17, 59, tzinfo=UTC),
        datetime(2026, 1, 7, 18, tzinfo=UTC),
    ):
        with pytest.raises(SessionCalendarError, match="outside verified calendar coverage"):
            configured.derive(timestamp)


@pytest.mark.parametrize("weekdays", [{-1}, {7}, {True}, {"0"}, {1.5}])
def test_invalid_weekdays_are_rejected(weekdays: set[object]) -> None:
    with pytest.raises(SessionCalendarError, match="weekdays"):
        calendar(trading_weekdays=weekdays)


@pytest.mark.parametrize("bounds", [
    {"coverage_start": date(2026, 1, 5)},
    {"coverage_end": date(2026, 1, 5)},
    {"coverage_start": date(2026, 1, 6), "coverage_end": date(2026, 1, 5)},
    {"coverage_start": datetime(2026, 1, 5), "coverage_end": datetime(2026, 1, 6)},
    {"coverage_start": "2026-01-05", "coverage_end": "2026-01-06"},
])
def test_invalid_coverage_is_rejected(bounds: dict[str, object]) -> None:
    with pytest.raises(SessionCalendarError, match="coverage"):
        calendar(**bounds)


def test_calendar_freezes_weekdays_and_component_resets_with_coverage() -> None:
    weekdays = {0}
    configured = calendar(
        trading_weekdays=weekdays,
        coverage_start=date(2026, 1, 5), coverage_end=date(2026, 1, 6),
    )
    weekdays.add(1)
    assert configured.trading_weekdays == frozenset({0})
    run_config = DetectionAnalysisConfig(
        instrument_id="US30", timeframe=Timeframe.M1, calendar_id=configured.calendar_id
    )
    component = SessionComponent(run_config, configured, pinned_calendar_version="fixture-v1")
    outputs = []
    for timestamp in (
        datetime(2026, 1, 5, 8, tzinfo=UTC),
        datetime(2026, 1, 6, 8, tzinfo=UTC),
    ):
        bar = Bar("US30", Timeframe.M1, timestamp, Decimal(1), Decimal(1), Decimal(1), Decimal(1))
        component.update(bar)
        outputs.append((bar, component.debug_json()))
    assert component.session_state is not None and not component.session_state.is_open
    component.reset()
    assert component.session_state is None
    for bar, expected in outputs:
        component.update(bar)
        assert component.debug_json() == expected


def test_window_ending_at_boundary_advances_to_next_civil_date() -> None:
    configured = calendar(
        windows=(SessionWindow("provider", time(1), time(22)),),
        breaks=(SessionWindow("evening", time(22), time(0)),),
    )
    assert configured.derive(datetime(2026, 1, 5, 22, tzinfo=UTC)).is_break
    assert configured.derive(datetime(2026, 1, 5, 23, 59, tzinfo=UTC)).is_break
    assert not configured.derive(datetime(2026, 1, 6, 0, tzinfo=UTC)).is_break
