"""Immutable, checksum-verified Parquet dataset revisions under one local root."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from itertools import zip_longest
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from sqlalchemy import Connection

from market_analysis.domain.dataset_lineage import (
    BAR_CHECKSUM_VERSION,
    BarSequence,
    DatasetLineage,
    canonical_bar_checksum_ordered,
)
from market_analysis.domain.dataset_validation import ValidationStatus
from market_analysis.domain.market_data import Bar, Timeframe

from .market_data import (
    DatasetRevision,
    load_dataset_lineage,
    load_dataset_revision,
    register_dataset_lineage,
    register_dataset_revision,
)

DATASET_FORMAT_VERSION = "parquet-v1"
_MANIFEST_NAME = "manifest.json"
_PARQUET_NAME = "bars.parquet"
_SAFE_REVISION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}\Z")
_ROW_GROUP_SIZE = 16_384
_SCHEMA = pa.schema((
    pa.field("instrument_id", pa.string(), nullable=False),
    pa.field("timeframe", pa.string(), nullable=False),
    pa.field("timestamp", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("open", pa.string(), nullable=False),
    pa.field("high", pa.string(), nullable=False),
    pa.field("low", pa.string(), nullable=False),
    pa.field("close", pa.string(), nullable=False),
    pa.field("volume", pa.string()),
    pa.field("source_id", pa.string(), nullable=False),
    pa.field("is_complete", pa.bool_(), nullable=False),
    pa.field("quality_flags", pa.list_(pa.string()), nullable=False),
))


class DatasetStoreError(ValueError):
    """A local revision is invalid, unavailable, conflicting, or corrupted."""


def _stamp(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            + "\n").encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_revision_id(value: str) -> str:
    if not _SAFE_REVISION_ID.fullmatch(value) or value in {".", ".."}:
        raise DatasetStoreError("dataset revision id is unsafe for a local path")
    return value


def _relative_manifest_ref(revision_id: str) -> str:
    return f"datasets/{_safe_revision_id(revision_id)}/{_MANIFEST_NAME}"


def _row(bar: Bar) -> dict[str, object]:
    return {
        "instrument_id": bar.instrument_id,
        "timeframe": bar.timeframe.value,
        "timestamp": bar.timestamp,
        "open": format(bar.open, "f"),
        "high": format(bar.high, "f"),
        "low": format(bar.low, "f"),
        "close": format(bar.close, "f"),
        "volume": None if bar.volume is None else format(bar.volume, "f"),
        "source_id": bar.source_id,
        "is_complete": bar.is_complete,
        "quality_flags": sorted(bar.quality_flags),
    }


def _bars_from_parquet(path: Path) -> Iterator[Bar]:
    try:
        parquet = pq.ParquetFile(path)
        if parquet.schema_arrow != _SCHEMA:
            raise DatasetStoreError("Parquet bar schema differs from parquet-v1")
        for batch in parquet.iter_batches(batch_size=_ROW_GROUP_SIZE):
            for row in batch.to_pylist():
                yield Bar(
                    instrument_id=row["instrument_id"],
                    timeframe=Timeframe(row["timeframe"]),
                    timestamp=row["timestamp"],
                    open=Decimal(row["open"]),
                    high=Decimal(row["high"]),
                    low=Decimal(row["low"]),
                    close=Decimal(row["close"]),
                    volume=None if row["volume"] is None else Decimal(row["volume"]),
                    source_id=row["source_id"],
                    is_complete=row["is_complete"],
                    quality_flags=frozenset(row["quality_flags"]),
                )
    except DatasetStoreError:
        raise
    except (OSError, ValueError, TypeError, pa.ArrowException) as exc:
        raise DatasetStoreError(f"cannot read canonical Parquet bars: {exc}") from exc


def _manifest(
    revision: DatasetRevision, lineage: DatasetLineage, parquet_sha256: str,
) -> dict[str, object]:
    return {
        "format_version": DATASET_FORMAT_VERSION,
        "dataset_revision_id": revision.dataset_revision_id,
        "dataset_id": revision.dataset_id,
        "source_id": revision.source_id,
        "provider": revision.provider,
        "retrieved_at": _stamp(revision.retrieved_at),
        "created_at": _stamp(revision.created_at),
        "normalization_version": revision.normalization_version,
        "calendar_version": revision.calendar_version,
        "instrument_id": lineage.instrument_id,
        "timeframe": lineage.timeframe.value,
        "requested_start": _stamp(lineage.requested_start),
        "requested_end": _stamp(lineage.requested_end),
        "actual_start": _stamp(lineage.actual_start),
        "actual_end": _stamp(lineage.actual_end),
        "bar_count": lineage.bar_count,
        "acquired_at": _stamp(lineage.acquired_at),
        "validation_status": lineage.validation_status.value,
        "provider_request": json.loads(lineage.provider_request_json),
        "source_checksum": lineage.source_checksum,
        "canonical_checksum": lineage.canonical_checksum,
        "checksum_version": lineage.checksum_version,
        "parquet_file": _PARQUET_NAME,
        "parquet_sha256": parquet_sha256,
    }


def _validate_metadata(revision: DatasetRevision, lineage: DatasetLineage) -> None:
    if (
        revision.manifest_format_version != DATASET_FORMAT_VERSION
        or lineage.dataset_format_version != DATASET_FORMAT_VERSION
    ):
        raise DatasetStoreError("only parquet-v1 revisions can be published")
    if revision.manifest_ref != _relative_manifest_ref(revision.dataset_revision_id):
        raise DatasetStoreError("revision manifest path is not canonical")
    if len(revision.memberships) != 1:
        raise DatasetStoreError("parquet-v1 publication requires one membership per revision")
    member = revision.memberships[0]
    if (
        lineage.dataset_revision_id != revision.dataset_revision_id
        or lineage.source_dataset_id != revision.source_id
        or (lineage.instrument_id, lineage.timeframe, lineage.requested_start,
            lineage.requested_end, lineage.bar_count)
        != (member.instrument_id, member.timeframe, member.range_start,
            member.range_end, member.bar_count)
        or lineage.checksum_version != BAR_CHECKSUM_VERSION
    ):
        raise DatasetStoreError("revision membership and content lineage differ")
    if lineage.validation_status is ValidationStatus.FAIL:
        raise DatasetStoreError("failed validation cannot publish a dataset revision")


def _write_parquet(path: Path, lineage: DatasetLineage, bars: Iterable[Bar]) -> None:
    batch: list[dict[str, object]] = []
    previous: Bar | None = None
    first: datetime | None = None
    count = 0
    with pq.ParquetWriter(path, _SCHEMA, compression="zstd") as writer:
        for bar in bars:
            if not isinstance(bar, Bar):
                raise DatasetStoreError("dataset input must contain canonical Bars")
            if (bar.instrument_id, bar.timeframe) != (lineage.instrument_id, lineage.timeframe):
                raise DatasetStoreError("dataset bar identity differs from lineage")
            if not bar.is_complete:
                raise DatasetStoreError("incomplete bars cannot be published")
            if not lineage.requested_start <= bar.timestamp < lineage.requested_end:
                raise DatasetStoreError("dataset bar is outside the requested range")
            if previous is not None:
                if bar.timestamp < previous.timestamp:
                    raise DatasetStoreError("dataset bars must be timestamp ordered")
                if bar.timestamp == previous.timestamp:
                    if bar != previous:
                        raise DatasetStoreError("conflicting duplicate bar at the same timestamp")
                    continue
            if first is None:
                first = bar.timestamp
            batch.append(_row(bar))
            count += 1
            previous = bar
            if len(batch) == _ROW_GROUP_SIZE:
                writer.write_table(pa.Table.from_pylist(batch, schema=_SCHEMA))
                batch.clear()
        if batch:
            writer.write_table(pa.Table.from_pylist(batch, schema=_SCHEMA))
    if count != lineage.bar_count or first != lineage.actual_start or (
        previous is not None and (
            lineage.actual_end is None or previous.timestamp >= lineage.actual_end
        )
    ):
        raise DatasetStoreError("published bar count or actual range differs from lineage")
    try:
        checksum = canonical_bar_checksum_ordered(_bars_from_parquet(path))
    except ValueError as exc:
        raise DatasetStoreError(f"published Parquet content is invalid: {exc}") from exc
    if checksum != lineage.canonical_checksum:
        raise DatasetStoreError("published Parquet checksum differs from lineage")


@dataclass(frozen=True, slots=True)
class DatasetStore:
    """Publish and resolve immutable revisions below a configured local root."""

    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).expanduser().resolve())

    def publish(
        self,
        connection: Connection,
        revision: DatasetRevision,
        lineage: DatasetLineage,
        bars: Iterable[Bar],
    ) -> DatasetRevision:
        """Write files first, then register metadata in the caller's transaction.

        An outer transaction rollback can leave an unreferenced revision
        directory. A matching retry reuses it; a conflicting retry never
        overwrites it. Callers own the outer database commit.
        """
        _validate_metadata(revision, lineage)
        datasets = self.root / "datasets"
        if datasets.is_symlink():
            raise DatasetStoreError("dataset root must not be a symlink")
        datasets.mkdir(parents=True, exist_ok=True)
        target = datasets / revision.dataset_revision_id
        stage = Path(tempfile.mkdtemp(prefix=".staging-", dir=datasets))
        try:
            parquet_path = stage / _PARQUET_NAME
            _write_parquet(parquet_path, lineage, bars)
            expected_manifest = _manifest(revision, lineage, _sha256_file(parquet_path))
            (stage / _MANIFEST_NAME).write_bytes(_canonical_json(expected_manifest))
            if target.exists() or target.is_symlink():
                existing = self._read_manifest(target)
                if {
                    key: value for key, value in existing.items() if key != "parquet_sha256"
                } != {
                    key: value for key, value in expected_manifest.items()
                    if key != "parquet_sha256"
                }:
                    raise DatasetStoreError("existing revision has different immutable metadata")
                self._verify_files(target, existing)
                if any(
                    old != new for old, new in zip_longest(
                        _bars_from_parquet(target / _PARQUET_NAME),
                        _bars_from_parquet(parquet_path),
                    )
                ):
                    raise DatasetStoreError("existing revision has different immutable bars")
            else:
                os.rename(stage, target)
            with connection.begin_nested():
                register_dataset_revision(connection, revision)
                register_dataset_lineage(connection, lineage)
        except (OSError, pa.ArrowException) as exc:
            raise DatasetStoreError(f"cannot publish dataset revision: {exc}") from exc
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        return revision

    def load_sequence(
        self, connection: Connection, revision_id: str, instrument_id: str,
        timeframe: Timeframe,
    ) -> BarSequence:
        """Resolve a revision offline and authenticate files against PostgreSQL."""
        lineage = load_dataset_lineage(connection, revision_id, instrument_id, timeframe)
        if lineage is None:
            raise DatasetStoreError("dataset lineage is unavailable")
        try:
            return BarSequence(
                lineage, tuple(self.iter_verified_bars(
                    connection, revision_id, instrument_id, timeframe,
                )),
            )
        except ValueError as exc:
            raise DatasetStoreError(f"dataset content differs from lineage: {exc}") from exc

    def iter_verified_bars(
        self, connection: Connection, revision_id: str, instrument_id: str,
        timeframe: Timeframe,
    ) -> Iterator[Bar]:
        """Verify the full revision with bounded memory, then stream its bars offline."""
        revision = load_dataset_revision(connection, revision_id)
        lineage = load_dataset_lineage(connection, revision_id, instrument_id, timeframe)
        if revision is None or lineage is None:
            raise DatasetStoreError("dataset revision or lineage is unavailable")
        _validate_metadata(revision, lineage)
        target = self.root / "datasets" / _safe_revision_id(revision_id)
        manifest = self._read_manifest(target)
        expected = _manifest(revision, lineage, str(manifest.get("parquet_sha256", "")))
        self._verify_files(target, expected)
        path = target / _PARQUET_NAME
        first: datetime | None = None
        last: datetime | None = None
        count = 0

        def checked_bars() -> Iterator[Bar]:
            nonlocal first, last, count
            for bar in _bars_from_parquet(path):
                if (bar.instrument_id, bar.timeframe) != (
                    lineage.instrument_id, lineage.timeframe,
                ) or not lineage.requested_start <= bar.timestamp < lineage.requested_end:
                    raise DatasetStoreError("Parquet bar identity or range differs from lineage")
                if first is None:
                    first = bar.timestamp
                last = bar.timestamp
                count += 1
                yield bar

        try:
            checksum = canonical_bar_checksum_ordered(checked_bars())
        except ValueError as exc:
            raise DatasetStoreError(f"dataset content differs from lineage: {exc}") from exc
        if (checksum != lineage.canonical_checksum or count != lineage.bar_count
            or first != lineage.actual_start or
            (last is not None and
             (lineage.actual_end is None or last >= lineage.actual_end))):
            raise DatasetStoreError("Parquet bar count, range or checksum differs from lineage")
        return _bars_from_parquet(path)

    def verify_revision(self, connection: Connection, revision_id: str) -> None:
        """Verify every membership of one database-referenced revision."""
        revision = load_dataset_revision(connection, revision_id)
        if revision is None:
            raise DatasetStoreError("dataset revision is unavailable")
        for member in revision.memberships:
            self.iter_verified_bars(
                connection, revision_id, member.instrument_id, member.timeframe,
            )

    @staticmethod
    def _read_manifest(target: Path) -> Mapping[str, Any]:
        if target.is_symlink() or not target.is_dir():
            raise DatasetStoreError("dataset revision directory is unavailable or unsafe")
        path = target / _MANIFEST_NAME
        if path.is_symlink() or not path.is_file():
            raise DatasetStoreError("dataset manifest is unavailable or unsafe")
        try:
            payload = path.read_bytes()
            manifest = json.loads(payload)
        except (OSError, UnicodeError, ValueError) as exc:
            raise DatasetStoreError(f"dataset manifest cannot be read: {exc}") from exc
        if not isinstance(manifest, dict) or payload != _canonical_json(manifest):
            raise DatasetStoreError("dataset manifest is not canonical JSON")
        if manifest.get("format_version") != DATASET_FORMAT_VERSION:
            raise DatasetStoreError("unsupported dataset format version")
        return manifest

    def _verify_files(self, target: Path, expected: Mapping[str, object]) -> None:
        manifest = self._read_manifest(target)
        if manifest != expected:
            raise DatasetStoreError("dataset manifest differs from persisted lineage")
        parquet_path = target / _PARQUET_NAME
        if parquet_path.is_symlink() or not parquet_path.is_file():
            raise DatasetStoreError("dataset Parquet file is unavailable or unsafe")
        if _sha256_file(parquet_path) != manifest["parquet_sha256"]:
            raise DatasetStoreError("dataset Parquet file checksum differs from manifest")
