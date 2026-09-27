from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from market_analysis.domain import (
    BAR_CHECKSUM_VERSION,
    Bar,
    BarSequence,
    DatasetLineage,
    DomainValidationError,
    Instrument,
    ProviderSymbolMapping,
    Timeframe,
    ValidationStatus,
    canonical_bar_checksum,
    canonical_bar_checksum_ordered,
)
from market_analysis.persistence.market_data import (
    DatasetMembership,
    DatasetRevision,
    MetadataConflictError,
    load_dataset_lineage,
    register_dataset_lineage,
    register_dataset_revision,
    register_instrument,
)
from market_analysis.persistence.runs import metadata

START = datetime(2026, 9, 27, 12, tzinfo=UTC)


def bar(minute: int, close: str = "101", source_id: str = "feed-1") -> Bar:
    return Bar(
        "US30", Timeframe.M1, START + timedelta(minutes=minute),
        Decimal("100"), Decimal("102"), Decimal("99"), Decimal(close),
        source_id=source_id, quality_flags=frozenset({"checked"}),
    )


def lineage(bars: tuple[Bar, ...] = (bar(0), bar(1))) -> DatasetLineage:
    return DatasetLineage(
        dataset_revision_id="rev-1",
        source_dataset_id="source-import-1",
        instrument_id="US30",
        timeframe=Timeframe.M1,
        requested_start=START,
        requested_end=START + timedelta(hours=1),
        actual_start=bars[0].timestamp if bars else None,
        actual_end=bars[-1].timestamp + timedelta(minutes=1) if bars else None,
        bar_count=len(bars),
        acquired_at=START + timedelta(hours=2),
        validation_status=ValidationStatus.PASS,
        provider_request_json='{"end":"2026-09-27T13:00:00Z","symbol":"US30"}',
        source_checksum="a" * 64,
        canonical_checksum=canonical_bar_checksum(bars),
        checksum_version=BAR_CHECKSUM_VERSION,
        dataset_format_version="parquet-v1",
    )


def revision() -> DatasetRevision:
    return DatasetRevision(
        dataset_revision_id="rev-1", dataset_id="study-1", source_id="feed-1",
        provider="fixture", retrieved_at=START + timedelta(hours=2),
        created_at=START + timedelta(hours=2, seconds=1),
        normalization_version="1", calendar_version="cal-v1",
        manifest_format_version="1", manifest_ref="datasets/rev-1/manifest.json",
        memberships=(DatasetMembership(
            "US30", Timeframe.M1, START, START + timedelta(hours=1), 2,
        ),),
    )


def test_checksum_is_stable_for_order_source_and_decimal_scale() -> None:
    first = (bar(0), bar(1))
    assert canonical_bar_checksum(first) == (
        "a9c9e34952dffa690731726774ece74c115f8ba10ab7a80c6551114127c20e5c"
    )
    assert canonical_bar_checksum_ordered(iter(first)) == canonical_bar_checksum(first)
    assert canonical_bar_checksum(()) == (
        "f494cba4a56716495aa07eaf95d7835de98b5158a68bc0217452b59c3d7fe92e"
    )
    reordered = (replace(bar(1), close=Decimal("101.000"), source_id="feed-2"), bar(0))
    assert canonical_bar_checksum(first) == canonical_bar_checksum(reordered)
    assert canonical_bar_checksum(first) != canonical_bar_checksum((bar(0), bar(1, "100")))
    assert canonical_bar_checksum(first) != canonical_bar_checksum((bar(0),))


def test_checksum_rejects_ambiguous_or_invalid_sequence() -> None:
    with pytest.raises(DomainValidationError, match="duplicate"):
        canonical_bar_checksum((bar(0), bar(0)))
    with pytest.raises(DomainValidationError, match="unordered"):
        canonical_bar_checksum_ordered((bar(1), bar(0)))
    with pytest.raises(DomainValidationError, match="one instrument"):
        canonical_bar_checksum((bar(0), replace(bar(1), instrument_id="DAX")))
    with pytest.raises(DomainValidationError, match="completed"):
        canonical_bar_checksum((replace(bar(0), is_complete=False),))


def test_bar_sequence_carries_lineage_and_detects_mutation() -> None:
    bars = (bar(0), bar(1))
    selected = BarSequence(lineage(bars), bars)
    assert selected.lineage.source_dataset_id == "source-import-1"
    assert selected.bars == bars
    with pytest.raises(DomainValidationError, match="checksum"):
        BarSequence(lineage(bars), (bar(0), bar(1, "100")))
    with pytest.raises(DomainValidationError, match="strictly ordered"):
        BarSequence(lineage(bars), tuple(reversed(bars)))
    assert BarSequence(lineage(()), ()).bars == ()


def test_lineage_rejects_invalid_provenance_and_range() -> None:
    selected = lineage()
    with pytest.raises(DomainValidationError, match="canonical JSON"):
        replace(selected, provider_request_json='{"symbol": "US30"}')
    with pytest.raises(DomainValidationError, match="source_checksum"):
        replace(selected, source_checksum="not-a-digest")
    with pytest.raises(DomainValidationError, match="inside requested range"):
        replace(selected, actual_end=selected.requested_end + timedelta(minutes=1))


def test_lineage_metadata_roundtrip_and_conflict() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    instrument = Instrument(
        "US30", "US 30", "cal-v1", 1, Decimal("1"),
        (ProviderSymbolMapping("fixture", "US30"),),
    )
    selected = lineage()
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        assert load_dataset_lineage(connection, "rev-1", "US30", Timeframe.M1) is None
        register_instrument(connection, instrument)
        register_dataset_revision(connection, revision())
        assert register_dataset_lineage(connection, selected) == selected
        assert load_dataset_lineage(connection, "rev-1", "US30", Timeframe.M1) == selected
        assert register_dataset_lineage(connection, selected) == selected
        with pytest.raises(MetadataConflictError, match="already differs"):
            register_dataset_lineage(connection, replace(selected, source_checksum="b" * 64))
        with pytest.raises(MetadataConflictError, match="no revision membership"):
            register_dataset_lineage(connection, replace(selected, dataset_revision_id="rev-2"))
    engine.dispose()
