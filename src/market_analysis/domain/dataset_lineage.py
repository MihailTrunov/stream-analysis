"""Versioned canonical-bar identity and immutable dataset lineage values."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from .dataset_validation import ValidationStatus
from .market_data import Bar, DomainValidationError, Timeframe

BAR_CHECKSUM_VERSION = "bar-sequence-v1"


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _digest(value: str, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        char not in "0123456789abcdef" for char in value
    ):
        raise DomainValidationError(f"{name} must be a lowercase SHA-256 hex digest")
    return value


def canonical_bar_checksum(bars: Iterable[Bar]) -> str:
    """Hash analytical Bar content, independent of input and provider ordering.

    The prefix versions both the schema and serialization. `source_id` is
    provenance and is deliberately excluded; source bytes have a separate
    checksum in DatasetLineage.
    """
    candidates = tuple(bars)
    if any(not isinstance(bar, Bar) for bar in candidates):
        raise DomainValidationError("checksum input must contain canonical Bars")
    return canonical_bar_checksum_ordered(sorted(candidates, key=lambda bar: bar.timestamp))


def canonical_bar_checksum_ordered(bars: Iterable[Bar]) -> str:
    """Stream the same checksum from already ordered canonical bars using bounded memory."""
    digest = sha256()
    digest.update(f"{BAR_CHECKSUM_VERSION}\n[".encode())
    identity: tuple[str, Timeframe] | None = None
    previous: datetime | None = None
    for index, bar in enumerate(bars):
        if not isinstance(bar, Bar):
            raise DomainValidationError("checksum input must contain canonical Bars")
        if identity is None:
            identity = (bar.instrument_id, bar.timeframe)
        elif (bar.instrument_id, bar.timeframe) != identity:
            raise DomainValidationError("checksum input must have one instrument/timeframe")
        if not bar.is_complete:
            raise DomainValidationError("checksum input must contain completed bars")
        if previous is not None and bar.timestamp <= previous:
            raise DomainValidationError("checksum input has duplicate or unordered timestamps")
        previous = bar.timestamp
        record = dict(bar.to_canonical_dict())
        record.pop("source_id")
        if index:
            digest.update(b",")
        digest.update(
            json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        )
    digest.update(b"]")
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class DatasetLineage:
    dataset_revision_id: str
    source_dataset_id: str
    instrument_id: str
    timeframe: Timeframe
    requested_start: datetime
    requested_end: datetime
    actual_start: datetime | None
    actual_end: datetime | None
    bar_count: int
    acquired_at: datetime
    validation_status: ValidationStatus
    provider_request_json: str
    source_checksum: str
    canonical_checksum: str
    checksum_version: str
    dataset_format_version: str

    def __post_init__(self) -> None:
        for name in (
            "dataset_revision_id", "source_dataset_id", "instrument_id",
            "checksum_version", "dataset_format_version",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise DomainValidationError(f"{name} must be non-empty")
        if not isinstance(self.timeframe, Timeframe):
            raise DomainValidationError("timeframe must be canonical")
        if not isinstance(self.validation_status, ValidationStatus):
            raise DomainValidationError("validation_status must be canonical")
        for name in ("requested_start", "requested_end", "acquired_at"):
            object.__setattr__(self, name, _utc(getattr(self, name), name))
        if self.requested_end < self.requested_start:
            raise DomainValidationError("requested_end must be >= requested_start")
        if (self.actual_start is None) != (self.actual_end is None):
            raise DomainValidationError("actual range must have both boundaries or neither")
        if self.actual_start is not None and self.actual_end is not None:
            object.__setattr__(self, "actual_start", _utc(self.actual_start, "actual_start"))
            object.__setattr__(self, "actual_end", _utc(self.actual_end, "actual_end"))
            if not (
                self.requested_start <= self.actual_start < self.actual_end <= self.requested_end
            ):
                raise DomainValidationError("actual range must lie inside requested range")
        if (
            isinstance(self.bar_count, bool)
            or not isinstance(self.bar_count, int)
            or self.bar_count < 0
        ):
            raise DomainValidationError("bar_count must be a non-negative integer")
        if (self.bar_count == 0) != (self.actual_start is None):
            raise DomainValidationError("empty data must have no actual range")
        try:
            provider_request = json.loads(self.provider_request_json)
        except (TypeError, ValueError) as exc:
            raise DomainValidationError("provider_request_json must be canonical JSON") from exc
        if not isinstance(provider_request, dict) or not provider_request:
            raise DomainValidationError("provider_request_json must contain request metadata")
        try:
            canonical_request = json.dumps(
                provider_request, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        except (TypeError, ValueError) as exc:
            raise DomainValidationError("provider_request_json must be canonical JSON") from exc
        if canonical_request != self.provider_request_json:
            raise DomainValidationError("provider_request_json must be canonical JSON")
        _digest(self.source_checksum, "source_checksum")
        _digest(self.canonical_checksum, "canonical_checksum")
        if self.checksum_version != BAR_CHECKSUM_VERSION:
            raise DomainValidationError("unsupported checksum_version")


@dataclass(frozen=True, slots=True)
class BarSequence:
    """An ordered immutable sequence with its independently persisted lineage."""

    lineage: DatasetLineage
    bars: tuple[Bar, ...]

    def __post_init__(self) -> None:
        bars = tuple(self.bars)
        if any(not isinstance(bar, Bar) for bar in bars):
            raise DomainValidationError("BarSequence must contain canonical Bars")
        if len(bars) != self.lineage.bar_count:
            raise DomainValidationError("BarSequence count differs from lineage")
        previous: datetime | None = None
        for bar in bars:
            if (bar.instrument_id, bar.timeframe) != (
                self.lineage.instrument_id, self.lineage.timeframe
            ):
                raise DomainValidationError("BarSequence identity differs from lineage")
            if previous is not None and bar.timestamp <= previous:
                raise DomainValidationError("BarSequence bars must be strictly ordered")
            previous = bar.timestamp
        if bars:
            if self.lineage.actual_start != bars[0].timestamp:
                raise DomainValidationError("BarSequence actual_start differs from first bar")
            if self.lineage.actual_end is None or bars[-1].timestamp >= self.lineage.actual_end:
                raise DomainValidationError("BarSequence actual_end must follow last bar")
        if canonical_bar_checksum_ordered(bars) != self.lineage.canonical_checksum:
            raise DomainValidationError("BarSequence checksum differs from lineage")
        object.__setattr__(self, "bars", bars)
