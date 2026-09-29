from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from market_analysis.domain import (
    BAR_CHECKSUM_VERSION,
    Bar,
    DatasetLineage,
    Instrument,
    ProviderSymbolMapping,
    Timeframe,
    ValidationStatus,
    canonical_bar_checksum,
)
from market_analysis.persistence.dataset_store import (
    DATASET_FORMAT_VERSION,
    DatasetStore,
    DatasetStoreError,
)
from market_analysis.persistence.market_data import (
    DatasetMembership,
    DatasetRevision,
    load_dataset_revision,
    register_instrument,
)
from market_analysis.persistence.runs import metadata

START = datetime(2026, 9, 27, 12, tzinfo=UTC)


def _bar(minute: int, close: str = "101") -> Bar:
    return Bar(
        "US30", Timeframe.M1, START + timedelta(minutes=minute),
        Decimal("100.00000000000000000001"), Decimal("102"), Decimal("99"),
        Decimal(close), Decimal("12.5"), source_id="fixture",
        quality_flags=frozenset({"verified"}),
    )


def _metadata(
    bars: tuple[Bar, ...], revision_id: str = "rev-1",
) -> tuple[DatasetRevision, DatasetLineage]:
    revision = DatasetRevision(
        revision_id, "study-1", "import-1", "fixture", START + timedelta(hours=2),
        START + timedelta(hours=2, seconds=1), "normalizer-v1", "calendar-v1",
        DATASET_FORMAT_VERSION,
        (DatasetMembership("US30", Timeframe.M1, START, START + timedelta(hours=1), len(bars)),),
        f"datasets/{revision_id}/manifest.json",
    )
    lineage = DatasetLineage(
        revision_id, "import-1", "US30", Timeframe.M1, START,
        START + timedelta(hours=1), bars[0].timestamp if bars else None,
        bars[-1].timestamp + timedelta(minutes=1) if bars else None,
        len(bars), START + timedelta(hours=2), ValidationStatus.PASS,
        '{"account":"test","price":"M"}', "a" * 64,
        canonical_bar_checksum(bars), BAR_CHECKSUM_VERSION, DATASET_FORMAT_VERSION,
    )
    return revision, lineage


@pytest.fixture
def store_and_engine(tmp_path: Path) -> tuple[DatasetStore, object]:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        register_instrument(connection, Instrument(
            "US30", "US 30", "calendar-v1", 20, Decimal("0.00000000000000000001"),
            (ProviderSymbolMapping("fixture", "US30"),),
        ))
    yield DatasetStore(tmp_path), engine
    engine.dispose()


def test_parquet_manifest_golden_roundtrip_and_offline_resolution(store_and_engine) -> None:
    store, engine = store_and_engine
    bars = (_bar(0), _bar(1))
    revision, lineage = _metadata(bars)
    with engine.begin() as connection:
        store.publish(connection, revision, lineage, iter(bars))
        store.publish(connection, revision, lineage, iter(bars))
        assert store.load_sequence(connection, "rev-1", "US30", Timeframe.M1).bars == bars
        store.verify_revision(connection, "rev-1")
    manifest_path = store.root / "datasets/rev-1/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["format_version"] == "parquet-v1"
    assert manifest["provider_request"] == {"account": "test", "price": "M"}
    assert manifest["canonical_checksum"] == (
        "ba530a34bb987d028263d4ad632a968e5172262d1785f1926b6ca5affa13050b"
    )
    assert manifest["bar_count"] == 2
    assert manifest["source_checksum"] == "a" * 64
    assert manifest_path.read_bytes().endswith(b"\n")


def test_duplicate_collapse_conflict_and_order(store_and_engine) -> None:
    store, engine = store_and_engine
    bars = (_bar(0), _bar(1))
    revision, lineage = _metadata(bars)
    with engine.begin() as connection:
        store.publish(connection, revision, lineage, (bars[0], bars[0], bars[1]))
        assert store.load_sequence(connection, "rev-1", "US30", Timeframe.M1).bars == bars
        with pytest.raises(DatasetStoreError, match="conflicting duplicate"):
            store.publish(connection, revision, lineage, (bars[0], _bar(0, "100"), bars[1]))
        with pytest.raises(DatasetStoreError, match="timestamp ordered"):
            store.publish(connection, revision, lineage, reversed(bars))
    assert tuple((store.root / "datasets").glob(".staging-*")) == ()


def test_immutable_revision_and_corrected_content_new_revision(store_and_engine) -> None:
    store, engine = store_and_engine
    original = (_bar(0), _bar(1))
    changed = (_bar(0), _bar(1, "100"))
    revision, lineage = _metadata(original)
    with engine.begin() as connection:
        store.publish(connection, revision, lineage, original)
        changed_revision, changed_lineage = _metadata(changed)
        with pytest.raises(DatasetStoreError, match="different immutable metadata"):
            store.publish(connection, changed_revision, changed_lineage, changed)
        with pytest.raises(DatasetStoreError, match="different immutable bars"):
            store.publish(
                connection, revision, lineage,
                tuple(replace(bar, source_id="other-feed") for bar in original),
            )
        new_revision, new_lineage = _metadata(changed, "rev-2")
        store.publish(connection, new_revision, new_lineage, changed)
        assert store.load_sequence(connection, "rev-1", "US30", Timeframe.M1).bars == original
        assert store.load_sequence(connection, "rev-2", "US30", Timeframe.M1).bars == changed


def test_rejects_unknown_version_bad_metadata_and_corruption(store_and_engine) -> None:
    store, engine = store_and_engine
    bars = (_bar(0),)
    revision, lineage = _metadata(bars)
    with engine.begin() as connection:
        with pytest.raises(DatasetStoreError, match="only parquet-v1"):
            store.publish(connection, replace(revision, manifest_format_version="parquet-v2"),
                          lineage, bars)
        with pytest.raises(DatasetStoreError, match="failed validation"):
            store.publish(connection, revision,
                          replace(lineage, validation_status=ValidationStatus.FAIL), bars)
        with pytest.raises(DatasetStoreError, match="unsafe"):
            store.publish(connection, replace(revision, dataset_revision_id="../escape"),
                          lineage, bars)
        store.publish(connection, revision, lineage, bars)
        path = store.root / "datasets/rev-1/manifest.json"
        payload = json.loads(path.read_text())
        payload["format_version"] = "parquet-v999"
        path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
        with pytest.raises(DatasetStoreError, match="unsupported dataset format"):
            store.load_sequence(connection, "rev-1", "US30", Timeframe.M1)
        path.write_text(json.dumps(payload | {"format_version": "parquet-v1"},
                                   sort_keys=True, separators=(",", ":")) + "\n")
        parquet = store.root / "datasets/rev-1/bars.parquet"
        with parquet.open("ab") as stream:
            stream.write(b"corruption")
        with pytest.raises(DatasetStoreError, match="checksum differs"):
            store.load_sequence(connection, "rev-1", "US30", Timeframe.M1)
        assert load_dataset_revision(connection, "rev-1") == revision


def test_empty_revision_and_symlink_refusal(store_and_engine) -> None:
    store, engine = store_and_engine
    revision, lineage = _metadata(())
    with engine.begin() as connection:
        store.publish(connection, revision, lineage, ())
        assert store.load_sequence(connection, "rev-1", "US30", Timeframe.M1).bars == ()
    target = store.root / "datasets/rev-1"
    target.rename(store.root / "moved-revision")
    target.symlink_to(store.root / "moved-revision")
    with engine.connect() as connection:
        with pytest.raises(DatasetStoreError, match="unsafe"):
            store.load_sequence(connection, "rev-1", "US30", Timeframe.M1)
