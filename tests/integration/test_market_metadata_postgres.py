from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from alembic import command
from alembic.config import Config
from market_analysis.application.replay_run import create_replay_run, load_replay_context
from market_analysis.config import DetectionAnalysisConfig
from market_analysis.domain import BAR_CHECKSUM_VERSION, DatasetLineage, ValidationStatus
from market_analysis.domain.market_data import Instrument, ProviderSymbolMapping, Timeframe
from market_analysis.persistence.market_data import (
    DatasetMembership,
    DatasetRevision,
    MetadataConflictError,
    load_dataset_lineage,
    load_dataset_revision,
    load_instrument,
    register_dataset_lineage,
    register_dataset_revision,
    register_instrument,
)
from market_analysis.persistence.replay_runs import ReplayStatus, transition_replay_run


@pytest.mark.skipif(
    not os.getenv("STREAM_ANALYSIS_TEST_DATABASE_URL"),
    reason="PostgreSQL integration URL is not configured",
)
def test_market_metadata_migration_and_repository_on_postgres(monkeypatch) -> None:
    url = os.environ["STREAM_ANALYSIS_TEST_DATABASE_URL"]
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(url)
    suffix = uuid4().hex
    instrument_id = f"instrument-{suffix}"
    revision_id = f"revision-{suffix}"
    instrument = Instrument(
        instrument_id, "Test instrument", "calendar-v1", 22,
        Decimal("0.0000000000000000000001"),
        (ProviderSymbolMapping("fixture", f"symbol-{suffix}"),),
    )
    start = datetime(2026, 9, 27, tzinfo=UTC)
    revision = DatasetRevision(
        dataset_revision_id=revision_id,
        dataset_id=f"dataset-{suffix}",
        source_id="fixture-source",
        provider="fixture",
        retrieved_at=start,
        created_at=start + timedelta(seconds=1),
        normalization_version="1",
        calendar_version="calendar-v1",
        manifest_format_version="1",
        manifest_ref=f"datasets/{revision_id}/manifest.json",
        memberships=(
            DatasetMembership(instrument_id, Timeframe.M1, start, start + timedelta(hours=1), 60),
        ),
    )
    lineage = DatasetLineage(
        dataset_revision_id=revision_id,
        source_dataset_id=f"source-{suffix}",
        instrument_id=instrument_id,
        timeframe=Timeframe.M1,
        requested_start=start,
        requested_end=start + timedelta(hours=1),
        actual_start=start,
        actual_end=start + timedelta(hours=1),
        bar_count=60,
        acquired_at=start + timedelta(seconds=1),
        validation_status=ValidationStatus.PASS,
        provider_request_json='{"end":"2026-09-27T01:00:00Z","symbol":"US30"}',
        source_checksum="a" * 64,
        canonical_checksum="b" * 64,
        checksum_version=BAR_CHECKSUM_VERSION,
        dataset_format_version="parquet-v1",
    )
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "20260930_08"
            )
            assert register_instrument(connection, instrument) == instrument
            assert load_instrument(connection, instrument_id) == instrument
            assert register_instrument(connection, instrument) == instrument
            with pytest.raises(MetadataConflictError):
                register_instrument(connection, replace(instrument, price_precision=5))
            assert register_dataset_revision(connection, revision) == revision
            assert load_dataset_revision(connection, revision_id) == revision
            assert register_dataset_revision(connection, revision) == revision
            assert register_dataset_lineage(connection, lineage) == lineage
            assert load_dataset_lineage(
                connection, revision_id, instrument_id, Timeframe.M1
            ) == lineage
            run_id = uuid4()
            replay = create_replay_run(
                connection, run_id=run_id, dataset_revision_id=revision_id,
                detection_config=DetectionAnalysisConfig(
                    instrument_id=instrument_id, calendar_id="calendar-v1"
                ),
                selected_start=start, selected_end=start + timedelta(hours=1),
                created_at=start + timedelta(seconds=2), build_id="test-build",
            )
            assert load_replay_context(connection, run_id) == replay
            assert transition_replay_run(
                connection, run_id, ReplayStatus.RUNNING, at=start + timedelta(seconds=2)
            ).status is ReplayStatus.RUNNING
            with pytest.raises(DBAPIError, match="run snapshot is immutable"):
                with connection.begin_nested():
                    connection.execute(
                        text("UPDATE run_snapshots SET build_id = 'changed' WHERE run_id = :id"),
                        {"id": str(run_id)},
                    )
            with pytest.raises(DBAPIError, match="market metadata is immutable"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE dataset_content_lineage SET source_checksum = :checksum "
                            "WHERE dataset_revision_id = :id"
                        ),
                        {"checksum": "c" * 64, "id": revision_id},
                    )
            with pytest.raises(DBAPIError, match="market metadata is immutable"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE instruments SET calendar_id = 'changed' "
                            "WHERE instrument_id = :id"
                        ),
                        {"id": instrument_id},
                    )
            with pytest.raises(DBAPIError, match="market metadata is immutable"):
                with connection.begin_nested():
                    connection.execute(
                        text("DELETE FROM dataset_revisions WHERE dataset_revision_id = :id"),
                        {"id": revision_id},
                    )
            with pytest.raises(MetadataConflictError):
                register_dataset_revision(connection, replace(revision, source_id="changed"))
            missing = replace(revision, dataset_revision_id=f"missing-{suffix}", memberships=(
                replace(revision.memberships[0], instrument_id=f"missing-{suffix}"),
            ))
            with pytest.raises(IntegrityError):
                register_dataset_revision(connection, missing)
            assert load_dataset_revision(connection, f"missing-{suffix}") is None
            indexes = {
                index["name"] for index in inspect(connection).get_indexes("dataset_memberships")
            }
            assert "ix_dataset_membership_lookup" in indexes
        finally:
            transaction.rollback()
    engine.dispose()
