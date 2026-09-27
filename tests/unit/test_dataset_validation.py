from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from market_analysis.domain import (
    Bar,
    Check,
    DomainValidationError,
    Timeframe,
    ValidationReport,
    ValidationStatus,
    validate_dataset,
)

START = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)


class FixtureCalendar:
    calendar_id = "fixture-session"
    version = "v7"

    def __init__(self, minutes: tuple[int, ...]) -> None:
        self.minutes = minutes

    def expected_slots(
        self, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> tuple[datetime, ...]:
        assert instrument_id == "US30" and timeframe is Timeframe.M1
        return tuple(START + timedelta(minutes=n) for n in self.minutes)


def fixture() -> list[dict[str, object]]:
    return json.loads(Path("tests/fixtures/validation_candidates.json").read_text())


def validate(
    records: list[Bar | dict[str, object]], calendar: FixtureCalendar | None = None
) -> ValidationReport:
    return validate_dataset(
        dataset_id="revision-1", instrument_id="US30", timeframe=Timeframe.M1,
        start=START, end=START + timedelta(minutes=5), records=records,
        calendar=calendar or FixtureCalendar((0, 1, 2, 4)), expected_source_id="memory",
    )


def test_curated_defects_and_report_roundtrip() -> None:
    report = validate(fixture())
    assert report.status is ValidationStatus.FAIL
    assert report.counts["duplicate_interval"] == 2
    assert report.counts["missing_interval"] == 2  # invalid OHLC and absent slot
    assert report.counts["invalid_ohlc"] == 1
    assert report.counts["timestamp_order"] == 1
    assert report.counts["incomplete_bar"] == 1
    assert report.counts["quality_flag"] == 1
    assert report.counts["source_flag"] == 1
    assert [f.timestamp for f in report.findings if f.check is Check.MISSING_INTERVAL] == [
        START + timedelta(minutes=1), START + timedelta(minutes=4),
    ]
    payload = json.loads(json.dumps(report.to_dict()))
    assert ValidationReport.from_dict(payload) == report
    assert report.to_dict() == validate(fixture()).to_dict()
    assert payload["calendar_version"] == "v7"
    assert payload["checks_executed"] == [check.value for check in Check]


def test_closed_intervals_are_not_gaps_and_empty_window_passes() -> None:
    records = [Bar.from_canonical_dict(fixture()[0])]
    report = validate(records, FixtureCalendar((0,)))
    assert report.status is ValidationStatus.PASS
    assert report.counts["missing_interval"] == 0
    empty = validate([], FixtureCalendar(()))
    assert empty.status is ValidationStatus.PASS
    assert empty.expected_slot_count == 0


def test_valid_flags_are_warnings_and_raw_invalid_record_is_structured() -> None:
    candidate = fixture()[0]
    candidate["source_id"] = "other"
    candidate["quality_flags"] = ["late"]
    report = validate([candidate], FixtureCalendar((0,)))
    assert report.status is ValidationStatus.WARNING
    assert report.counts["warnings"] == 2

    candidate["timestamp"] = "bad-time"
    report = validate([candidate], FixtureCalendar(()))
    assert report.counts["invalid_record"] == 1
    assert report.findings[0].record_index == 0

    candidate["timestamp"] = "2026-01-02T14:30:00"  # naive input must stay a finding
    report = validate([candidate], FixtureCalendar(()))
    assert report.counts["invalid_record"] == 1

    candidate["timestamp"] = "2026-01-02T14:30:00Z"
    candidate["source_id"] = ""
    report = validate([candidate], FixtureCalendar((0,)))
    assert report.counts["source_flag"] == 1
    assert report.counts["missing_interval"] == 1


def test_calendar_must_supply_ordered_unique_in_range_utc_slots() -> None:
    with pytest.raises(DomainValidationError, match="calendar expected slots"):
        validate([], FixtureCalendar((1, 0)))
    with pytest.raises(DomainValidationError, match="calendar expected slots"):
        validate([], FixtureCalendar((0, 0)))
    with pytest.raises(DomainValidationError, match="calendar expected slots"):
        validate([], FixtureCalendar((5,)))


def test_report_rejects_modified_derived_counts() -> None:
    payload = validate([], FixtureCalendar(())).to_dict()
    counts = payload["counts"]
    assert isinstance(counts, dict)
    counts["errors"] = 1
    with pytest.raises(DomainValidationError, match="derived counts"):
        ValidationReport.from_dict(payload)
