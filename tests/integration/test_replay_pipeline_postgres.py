"""Provider-free replay execution against an isolated PostgreSQL test schema."""

from __future__ import annotations

import os
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from market_analysis.application import replay_pipeline
from market_analysis.application.replay_pipeline import ReplayPipeline, ReplayPipelineError
from market_analysis.application.replay_run import create_replay_run, load_replay_context
from market_analysis.config import ComponentSelection, ConfigParameter, DetectionAnalysisConfig
from market_analysis.detection import DetectorRuntime
from market_analysis.domain import (
    BAR_CHECKSUM_VERSION,
    Bar,
    DatasetLineage,
    Instrument,
    SessionWindow,
    Timeframe,
    TradingCalendar,
    ValidationStatus,
    canonical_bar_checksum,
)
from market_analysis.persistence.market_data import (
    DatasetMembership,
    DatasetRevision,
    register_dataset_lineage,
    register_dataset_revision,
    register_instrument,
)
from market_analysis.persistence.replay_runs import ReplayStatus
from market_analysis.persistence.runs import metadata


@pytest.mark.skipif(
    not os.getenv("STREAM_ANALYSIS_TEST_DATABASE_URL"),
    reason="PostgreSQL integration URL is not configured",
)
@pytest.mark.parametrize("fail_final_write", [False, True])
def test_seeded_execution_completion_and_failure_roundtrip_on_postgres(
    monkeypatch: pytest.MonkeyPatch, fail_final_write: bool,
) -> None:
    engine = create_engine(os.environ["STREAM_ANALYSIS_TEST_DATABASE_URL"])
    assert engine.dialect.name == "postgresql"
    schema = f"replay_test_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    scoped = engine.execution_options(schema_translate_map={None: schema})
    try:
        with scoped.begin() as connection:
            metadata.create_all(connection)
        start = datetime(2026, 1, 5, 12, tzinfo=UTC)
        end = start + timedelta(minutes=8)
        bars = tuple(
            Bar("US30", Timeframe.M1, start + timedelta(minutes=index),
                Decimal(100 + index), Decimal(102 + index), Decimal(99 + index),
                Decimal(101 + index))
            for index in range(8)
        )
        calendar = TradingCalendar(
            calendar_id="seed-calendar", version="v1", provider="seed", account="local",
            instrument_id="US30", timezone_name="Europe/London",
            trading_day_boundary=time(0),
            windows=(SessionWindow("london", time(8), time(17)),),
        )
        config = DetectionAnalysisConfig(
            instrument_id="US30", timeframe=Timeframe.M1, calendar_id=calendar.calendar_id,
            components=(
                ComponentSelection(
                    component_id="atr", component_version="1",
                    parameters=(ConfigParameter(name="period", value=1),),
                ),
                ComponentSelection(
                    component_id="ema", component_version="1",
                    parameters=(ConfigParameter(name="period", value=3),),
                ),
            ),
        )
        run_id = uuid4()
        with scoped.begin() as connection:
            register_instrument(connection, Instrument(
                "US30", "Seed instrument", calendar.calendar_id, 1, Decimal("1"),
            ))
            register_dataset_revision(connection, DatasetRevision(
                dataset_revision_id="seed-revision", dataset_id="seed-dataset",
                source_id="seed-source", provider="seed", retrieved_at=start, created_at=start,
                normalization_version="1", calendar_version=calendar.version,
                manifest_format_version="1", manifest_ref="datasets/seed/manifest.json",
                memberships=(DatasetMembership("US30", Timeframe.M1, start, end, len(bars)),),
            ))
            register_dataset_lineage(connection, DatasetLineage(
                dataset_revision_id="seed-revision", source_dataset_id="seed-source",
                instrument_id="US30", timeframe=Timeframe.M1,
                requested_start=start, requested_end=end, actual_start=start, actual_end=end,
                bar_count=len(bars), acquired_at=start, validation_status=ValidationStatus.PASS,
                provider_request_json='{"fixture":"replay"}', source_checksum="a" * 64,
                canonical_checksum=canonical_bar_checksum(bars),
                checksum_version=BAR_CHECKSUM_VERSION, dataset_format_version="parquet-v1",
            ))
            original = create_replay_run(
                connection, run_id=run_id, dataset_revision_id="seed-revision",
                detection_config=config, selected_start=start + timedelta(minutes=3),
                selected_end=end, created_at=start, build_id="seed-build",
            )
        with scoped.begin() as connection:
            pipeline = ReplayPipeline.from_run(
                connection, run_id, bars=bars, bindings_factory=lambda: (),
                calendar_resolver=lambda calendar_id, version: calendar,
            )
            pipeline.start(connection, at=start)
            direct = DetectorRuntime(
                pipeline.detection_config, (), run_id=str(run_id),
                dataset_revision_id="seed-revision", calendar=calendar,
                pinned_calendar_version=calendar.version,
                pinned_config_hash=pipeline.snapshot.detection_config_hash,
            )
            for index in range(len(bars) - 1):
                result = pipeline.step(connection)
                expected = direct.process_bar(bars[index])
                assert result.result.frame.debug_json() == expected.frame.debug_json()
            if fail_final_write:
                advance = replay_pipeline.advance_replay_cursor

                def advance_then_fail(connection, run_id, index):
                    advance(connection, run_id, index)
                    raise RuntimeError("injected PostgreSQL post-write failure")

                monkeypatch.setattr(replay_pipeline, "advance_replay_cursor", advance_then_fail)
                with pytest.raises(RuntimeError, match="post-write failure"):
                    pipeline.step(connection)
                with pytest.raises(ReplayPipelineError, match="latched"):
                    pipeline.run_to_completion(connection, at=end)
            else:
                final = pipeline.step(connection)
                expected = direct.process_bar(bars[-1])
                assert final.result.frame.debug_json() == expected.frame.debug_json()
                completed = pipeline.run_to_completion(connection, at=end)
                assert (completed.warmup_bars, completed.visible_bars) == (3, 5)
        with scoped.connect() as connection:
            reloaded = load_replay_context(connection, run_id)
            assert reloaded is not None
            assert reloaded.snapshot == original.snapshot
            assert reloaded.lineage == original.lineage
            assert reloaded.lifecycle.status is (
                ReplayStatus.FAILED if fail_final_write else ReplayStatus.COMPLETED
            )
            assert reloaded.lifecycle.cursor_index == len(bars) - (2 if fail_final_write else 1)
            if fail_final_write:
                assert "post-write failure" in str(reloaded.lifecycle.failure_reason)
            else:
                assert reloaded.lifecycle.failure_reason is None
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()
