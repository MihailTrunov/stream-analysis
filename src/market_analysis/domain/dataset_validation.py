"""Deterministic dataset checks over canonical bars or untrusted bar candidates.

The calendar supplies expected UTC slot starts for the explicit half-open window.
This module has no knowledge of instrument sessions or holidays.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from .market_data import Bar, DomainValidationError, Timeframe


class ExpectedSlotCalendar(Protocol):
    calendar_id: str
    version: str

    def expected_slots(
        self, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> Iterable[datetime]: ...


class Severity(StrEnum):
    WARNING = "warning"
    ERROR = "error"


class ValidationStatus(StrEnum):
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


class Check(StrEnum):
    DUPLICATE_INTERVAL = "duplicate_interval"
    MISSING_INTERVAL = "missing_interval"
    INVALID_OHLC = "invalid_ohlc"
    TIMESTAMP_ORDER = "timestamp_order"
    INCOMPLETE_BAR = "incomplete_bar"
    QUALITY_FLAG = "quality_flag"
    SOURCE_FLAG = "source_flag"
    INVALID_RECORD = "invalid_record"


CHECKS = tuple(Check)


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _stamp(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat().replace("+00:00", "Z")


def _parse_stamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _utc(value, "timestamp")
    if not isinstance(value, str):
        return None
    try:
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")), "timestamp")
    except (ValueError, DomainValidationError):
        return None


@dataclass(frozen=True, slots=True)
class Finding:
    check: Check
    severity: Severity
    message: str
    timestamp: datetime | None = None
    record_index: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "check": self.check.value,
            "severity": self.severity.value,
            "message": self.message,
            "timestamp": _stamp(self.timestamp),
            "record_index": self.record_index,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Finding:
        raw_stamp = data["timestamp"]
        timestamp = None if raw_stamp is None else _parse_stamp(raw_stamp)
        if raw_stamp is not None and timestamp is None:
            raise DomainValidationError("finding timestamp must be a UTC instant")
        index = data["record_index"]
        if index is not None and (isinstance(index, bool) or not isinstance(index, int)):
            raise DomainValidationError("record_index must be an integer or null")
        return cls(
            Check(str(data["check"])), Severity(str(data["severity"])),
            str(data["message"]), timestamp, index,
        )


@dataclass(frozen=True, slots=True)
class ValidationReport:
    dataset_id: str
    instrument_id: str
    timeframe: Timeframe
    start: datetime
    end: datetime
    calendar_id: str
    calendar_version: str
    checks_executed: tuple[Check, ...]
    record_count: int
    valid_bar_count: int
    expected_slot_count: int
    findings: tuple[Finding, ...]

    @property
    def counts(self) -> dict[str, int]:
        counts = Counter(finding.check.value for finding in self.findings)
        return {
            "records": self.record_count,
            "valid_bars": self.valid_bar_count,
            "expected_slots": self.expected_slot_count,
            "findings": len(self.findings),
            "errors": sum(f.severity is Severity.ERROR for f in self.findings),
            "warnings": sum(f.severity is Severity.WARNING for f in self.findings),
            **{check.value: counts[check.value] for check in CHECKS},
        }

    @property
    def status(self) -> ValidationStatus:
        if any(f.severity is Severity.ERROR for f in self.findings):
            return ValidationStatus.FAIL
        if self.findings:
            return ValidationStatus.WARNING
        return ValidationStatus.PASS

    def to_dict(self) -> dict[str, object]:
        return {
            "dataset_id": self.dataset_id,
            "instrument_id": self.instrument_id,
            "timeframe": self.timeframe.value,
            "start": _stamp(self.start),
            "end": _stamp(self.end),
            "calendar_id": self.calendar_id,
            "calendar_version": self.calendar_version,
            "checks_executed": [check.value for check in self.checks_executed],
            "counts": self.counts,
            "status": self.status.value,
            "findings": [finding.to_dict() for finding in self.findings],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ValidationReport:
        counts = data["counts"]
        checks = data["checks_executed"]
        findings = data["findings"]
        if (
            not isinstance(counts, Mapping)
            or not isinstance(checks, list)
            or not isinstance(findings, list)
        ):
            raise DomainValidationError("report counts, checks and findings must be structured")
        start, end = _parse_stamp(data["start"]), _parse_stamp(data["end"])
        if start is None or end is None:
            raise DomainValidationError("report window must contain UTC instants")
        report = cls(
            dataset_id=str(data["dataset_id"]),
            instrument_id=str(data["instrument_id"]),
            timeframe=Timeframe(str(data["timeframe"])),
            start=start, end=end,
            calendar_id=str(data["calendar_id"]),
            calendar_version=str(data["calendar_version"]),
            checks_executed=tuple(Check(str(value)) for value in checks),
            record_count=int(counts["records"]),
            valid_bar_count=int(counts["valid_bars"]),
            expected_slot_count=int(counts["expected_slots"]),
            findings=tuple(Finding.from_dict(value) for value in findings),
        )
        if report.to_dict() != data:
            raise DomainValidationError("report derived counts or status do not match findings")
        return report


def validate_dataset(
    *,
    dataset_id: str,
    instrument_id: str,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    records: Sequence[Bar | Mapping[str, object]],
    calendar: ExpectedSlotCalendar,
    expected_source_id: str | None = None,
) -> ValidationReport:
    """Inspect records in input order; never collapse, reorder or repair them."""
    if not dataset_id.strip() or not instrument_id.strip():
        raise DomainValidationError("dataset_id and instrument_id must be non-empty")
    if not isinstance(timeframe, Timeframe):
        raise DomainValidationError("timeframe must be canonical")
    start, end = _utc(start, "start"), _utc(end, "end")
    if end < start:
        raise DomainValidationError("end must be >= start")
    if not calendar.calendar_id.strip() or not calendar.version.strip():
        raise DomainValidationError("calendar id and version must be non-empty")
    if expected_source_id is not None and not expected_source_id.strip():
        raise DomainValidationError("expected_source_id must be non-empty")

    slots = tuple(calendar.expected_slots(instrument_id, timeframe, start, end))
    if any(
        not isinstance(slot, datetime)
        or slot.tzinfo is None
        or slot.utcoffset() is None
        or slot.utcoffset() != UTC.utcoffset(slot)
        or not start <= slot < end
        for slot in slots
    ) or tuple(sorted(set(slots))) != slots:
        raise DomainValidationError(
            "calendar expected slots must be unique, ordered UTC instants in range"
        )

    findings: list[Finding] = []
    seen: dict[datetime, int] = {}
    present: set[datetime] = set()
    previous: datetime | None = None
    valid_count = 0
    for index, record in enumerate(records):
        if isinstance(record, Bar):
            candidate: Mapping[str, object] = record.to_canonical_dict()
            bar: Bar | None = record
        elif isinstance(record, Mapping):
            candidate = record
            try:
                bar = Bar.from_canonical_dict(candidate)
            except (DomainValidationError, TypeError, AttributeError) as exc:
                bar = None
                message = str(exc)
                check = Check.INVALID_OHLC if any(
                    token in message for token in ("open", "high", "low", "close")
                ) else Check.INVALID_RECORD
                findings.append(
                    Finding(
                        check, Severity.ERROR, message,
                        _parse_stamp(candidate.get("timestamp")), index,
                    )
                )
                if "source_id" in message:
                    findings.append(
                        Finding(
                            Check.SOURCE_FLAG, Severity.ERROR, message,
                            _parse_stamp(candidate.get("timestamp")), index,
                        )
                    )
        else:
            raise DomainValidationError("records must contain Bars or candidate mappings")

        timestamp = bar.timestamp if bar is not None else _parse_stamp(candidate.get("timestamp"))
        if timestamp is not None:
            if not start <= timestamp < end:
                findings.append(
                    Finding(Check.INVALID_RECORD, Severity.ERROR,
                            "timestamp outside validation window", timestamp, index)
                )
            if previous is not None and timestamp < previous:
                findings.append(
                    Finding(Check.TIMESTAMP_ORDER, Severity.ERROR,
                            "timestamp precedes previous record", timestamp, index)
                )
            previous = timestamp
            if timestamp in seen:
                findings.append(
                    Finding(Check.DUPLICATE_INTERVAL, Severity.ERROR,
                            f"duplicate of record {seen[timestamp]}", timestamp, index)
                )
            else:
                seen[timestamp] = index

        if bar is None:
            continue
        if bar.instrument_id != instrument_id or bar.timeframe != timeframe:
            findings.append(
                Finding(Check.INVALID_RECORD, Severity.ERROR,
                        "instrument or timeframe mismatch", timestamp, index)
            )
            continue
        valid_count += 1
        if start <= bar.timestamp < end:
            present.add(bar.timestamp)
        if not bar.is_complete:
            findings.append(
                Finding(Check.INCOMPLETE_BAR, Severity.ERROR, "bar is incomplete", timestamp, index)
            )
        for flag in sorted(bar.quality_flags):
            findings.append(Finding(Check.QUALITY_FLAG, Severity.WARNING, flag, timestamp, index))
        if expected_source_id is not None and bar.source_id != expected_source_id:
            findings.append(
                Finding(Check.SOURCE_FLAG, Severity.WARNING,
                        f"unexpected source_id: {bar.source_id}", timestamp, index)
            )

    for slot in slots:
        if slot not in present:
            findings.append(
                Finding(
                    Check.MISSING_INTERVAL, Severity.ERROR,
                    "expected slot has no valid bar", slot,
                )
            )
    findings.sort(
        key=lambda f: (
            f.timestamp or datetime.min.replace(tzinfo=UTC),
            f.check.value,
            -1 if f.record_index is None else f.record_index,
            f.message,
        )
    )
    return ValidationReport(
        dataset_id, instrument_id, timeframe, start, end, calendar.calendar_id,
        calendar.version, CHECKS, len(records), valid_count, len(slots), tuple(findings),
    )
