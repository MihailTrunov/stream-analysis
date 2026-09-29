from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from alembic.config import Config
from market_analysis.domain.market_data import Instrument, ProviderSymbolMapping, Timeframe
from market_analysis.persistence.market_data import (
    DatasetMembership,
    DatasetRevision,
    MetadataConflictError,
    list_dataset_revisions,
    load_dataset_revision,
    load_instrument,
    register_dataset_revision,
    register_instrument,
)


def _instrument() -> Instrument:
    return Instrument(
        "US30", "US 30", "market-calendar-v1", 20,
        Decimal("0.00000000000000000001"),
        (ProviderSymbolMapping("OANDA", "US30_USD", "practice"),),
    )


def _revision(revision_id: str = "rev-1") -> DatasetRevision:
    start = datetime(2026, 9, 27, tzinfo=UTC)
    return DatasetRevision(
        dataset_revision_id=revision_id,
        dataset_id="research-data",
        source_id="import-1",
        provider="fixture",
        retrieved_at=start,
        created_at=start + timedelta(hours=1),
        normalization_version="1",
        calendar_version="market-calendar-v1",
        manifest_format_version="1",
        manifest_ref=f"datasets/{revision_id}/manifest.json",
        memberships=(
            DatasetMembership("US30", Timeframe.M1, start, start + timedelta(hours=1), 60),
        ),
    )


def test_instrument_roundtrip_duplicate_and_conflict() -> None:
    engine = create_engine("sqlite://")
    from market_analysis.persistence.runs import metadata

    metadata.create_all(engine)
    instrument = _instrument()
    with engine.begin() as connection:
        assert load_instrument(connection, "missing") is None
        assert register_instrument(connection, instrument) == instrument
        assert register_instrument(connection, instrument) == instrument
        assert load_instrument(connection, "US30") == instrument
        assert (
            connection.scalar(text("SELECT point_size FROM instruments"))
            == "0.00000000000000000001"
        )
        with pytest.raises(MetadataConflictError):
            register_instrument(connection, replace(instrument, calendar_id="other-calendar"))
        with pytest.raises(MetadataConflictError, match="already assigned"):
            register_instrument(
                connection,
                replace(instrument, instrument_id="OTHER"),
            )
        assert load_instrument(connection, "US30") == instrument
        assert load_instrument(connection, "OTHER") is None


def test_dataset_membership_roundtrip_and_foreign_key() -> None:
    engine = create_engine("sqlite://")
    from market_analysis.persistence.runs import metadata

    metadata.create_all(engine)
    revision = _revision()
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        assert load_dataset_revision(connection, "missing") is None
        with pytest.raises(IntegrityError):
            register_dataset_revision(connection, revision)
        register_instrument(connection, _instrument())
        assert register_dataset_revision(connection, revision) == revision
        assert register_dataset_revision(connection, revision) == revision
        assert load_dataset_revision(connection, revision.dataset_revision_id) == revision
        with pytest.raises(MetadataConflictError):
            register_dataset_revision(connection, replace(revision, source_id="other"))
        register_dataset_revision(connection, _revision("rev-0"))
        ids = tuple(
            r.dataset_revision_id for r in list_dataset_revisions(connection, "research-data")
        )
        assert ids == (
            "rev-0", "rev-1"
        )
        assert list_dataset_revisions(connection, "missing") == ()


def test_membership_rejects_invalid_ranges_and_duplicates() -> None:
    start = datetime(2026, 9, 27, tzinfo=UTC)
    with pytest.raises(ValueError, match="after"):
        DatasetMembership("US30", Timeframe.M1, start, start, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        DatasetMembership("US30", Timeframe.M1, start.replace(tzinfo=None), start, 0)
    with pytest.raises(ValueError, match="unique"):
        member = _revision().memberships[0]
        replace(_revision(), memberships=(member, member))
    with pytest.raises(ValueError, match="manifest_ref"):
        replace(_revision(), manifest_ref="")


def test_market_metadata_migration_is_reversible(monkeypatch, tmp_path) -> None:
    path = tmp_path / "market-metadata.db"
    url = f"sqlite:///{path}"
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    engine = create_engine(url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260929_05"
        assert connection.scalar(text("SELECT count(*) FROM dataset_revisions")) == 0
    command.downgrade(config, "20260926_01")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260926_01"
        assert connection.scalar(text("SELECT count(*) FROM run_snapshots")) == 0
    engine.dispose()
