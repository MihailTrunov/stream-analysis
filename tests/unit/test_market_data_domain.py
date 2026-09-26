from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from market_analysis.domain import (
    Bar,
    DomainValidationError,
    Instrument,
    ProviderSymbolMapping,
    Timeframe,
)


def test_bar_normalizes_timezone_and_round_trips() -> None:
    local = timezone(timedelta(hours=2))
    bar = Bar(
        instrument_id="US30",
        timeframe=Timeframe.M1,
        timestamp=datetime(2026, 1, 2, 16, 30, tzinfo=local),
        open=Decimal("42000.00"),
        high=Decimal("42005.00"),
        low=Decimal("41998.00"),
        close=Decimal("42003.00"),
        volume=Decimal("12"),
        source_id="oanda",
        quality_flags=frozenset({"provider_complete"}),
    )

    assert bar.timestamp == datetime(2026, 1, 2, 14, 30, tzinfo=UTC)
    payload = dict(bar.to_canonical_dict())
    assert payload["timestamp"] == "2026-01-02T14:30:00Z"
    assert Bar.from_canonical_dict(payload) == bar


def test_equivalent_numeric_inputs_normalize_to_equal_bar_values() -> None:
    timestamp = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)
    from_strings = Bar("US30", Timeframe.M1, timestamp, "1.20", "1.30", "1.10", "1.25")
    from_decimals = Bar(
        "US30",
        Timeframe.M1,
        timestamp,
        Decimal("1.20"),
        Decimal("1.30"),
        Decimal("1.10"),
        Decimal("1.25"),
    )
    assert from_strings == from_decimals
    assert from_strings.identity == ("US30", Timeframe.M1, timestamp)


@pytest.mark.parametrize(
    ("high", "low", "message"),
    [
        ("0.9", "0.8", "high must be >= open and close"),
        ("1.2", "1.1", "low must be <= open and close"),
    ],
)
def test_invalid_ohlc_is_rejected(high: str, low: str, message: str) -> None:
    with pytest.raises(DomainValidationError, match=message):
        Bar(
            "US30",
            Timeframe.M1,
            datetime(2026, 1, 2, tzinfo=UTC),
            "1.0",
            high,
            low,
            "1.0",
        )


def test_naive_timestamp_and_non_finite_price_are_rejected() -> None:
    with pytest.raises(DomainValidationError, match="timezone-aware"):
        Bar("US30", Timeframe.M1, datetime(2026, 1, 2), "1", "1", "1", "1")
    with pytest.raises(DomainValidationError, match="finite decimal"):
        Bar(
            "US30",
            Timeframe.M1,
            datetime(2026, 1, 2, tzinfo=UTC),
            "NaN",
            "1",
            "1",
            "1",
        )


def test_instrument_is_hashable_and_provider_mapping_is_unambiguous() -> None:
    instrument = Instrument(
        instrument_id="US30",
        display_name="US 30",
        calendar_id="oanda-us30-v1",
        price_precision=1,
        point_size=Decimal("1"),
        provider_symbols=(ProviderSymbolMapping("oanda", "US30_USD", "practice"),),
    )
    assert instrument.provider_symbol("OANDA", environment="PRACTICE") == "US30_USD"
    assert hash(instrument)

    with pytest.raises(DomainValidationError, match="mappings must be unique"):
        Instrument(
            "US30",
            "US 30",
            "calendar",
            1,
            Decimal("1"),
            (
                ProviderSymbolMapping("OANDA", "A", "practice"),
                ProviderSymbolMapping("oanda", "B", "PRACTICE"),
            ),
        )


def test_unsupported_timeframe_is_rejected() -> None:
    with pytest.raises(DomainValidationError, match="unsupported timeframe"):
        Bar(  # type: ignore[arg-type]
            "US30",
            "2m",
            datetime(2026, 1, 2, tzinfo=UTC),
            "1",
            "1",
            "1",
            "1",
        )


def test_mutable_inputs_are_frozen_on_construction() -> None:
    mappings = [ProviderSymbolMapping("oanda", "US30_USD")]
    instrument = Instrument("US30", "US 30", "calendar", 1, Decimal("1"), mappings)  # type: ignore[arg-type]
    mappings.append(ProviderSymbolMapping("other", "US30"))
    assert len(instrument.provider_symbols) == 1

    flags = {"complete"}
    bar = Bar(
        "US30",
        Timeframe.M1,
        datetime(2026, 1, 2, tzinfo=UTC),
        "1",
        "1",
        "1",
        "1",
        quality_flags=flags,  # type: ignore[arg-type]
    )
    flags.add("mutated")
    assert bar.quality_flags == frozenset({"complete"})
    assert hash(bar)


def test_round_trip_rejects_invalid_timeframe_and_flag_types() -> None:
    base = dict(
        Bar(
            "US30",
            Timeframe.M1,
            datetime(2026, 1, 2, tzinfo=UTC),
            "1",
            "1",
            "1",
            "1",
        ).to_canonical_dict()
    )
    base["timeframe"] = "2m"
    with pytest.raises(DomainValidationError, match="unsupported timeframe"):
        Bar.from_canonical_dict(base)

    base["timeframe"] = "1m"
    base["quality_flags"] = [1]
    with pytest.raises(DomainValidationError, match="contain strings"):
        Bar.from_canonical_dict(base)
