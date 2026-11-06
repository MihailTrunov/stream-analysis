"""Real PostgreSQL live input attribution and stopped-service archive restoration."""

import json
import os
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from alembic import command
from alembic.config import Config
from market_analysis.application.history_ranges import HistoryFeed
from market_analysis.application.live_analysis import LiveAnalysisManager, LiveAnalysisRequest
from market_analysis.application.live_ingress import LiveIngress
from market_analysis.application.live_inputs import read_live_input, verify_live_inputs
from market_analysis.application.live_retention import feed_key, verify_live_retention
from market_analysis.application.replay_catalog import demo_detection_config
from market_analysis.config.oanda_uk_calendars import (
    build_oanda_uk_calendar,
    build_oanda_uk_instrument,
)
from market_analysis.domain import (
    Bar,
    DatasetLineage,
    HistoricalDataPage,
    HistoricalSource,
    Timeframe,
)
from market_analysis.domain.dataset_lineage import (
    BAR_CHECKSUM_VERSION,
    canonical_bar_checksum_ordered,
)
from market_analysis.domain.dataset_validation import ValidationStatus
from market_analysis.domain.live_data import LiveCandleUpdate
from market_analysis.persistence.backup_archive import (
    create_archive,
    extract_verified_archive,
    verify_archive,
)
from market_analysis.persistence.dataset_store import DATASET_FORMAT_VERSION, DatasetStore
from market_analysis.persistence.market_data import (
    DatasetMembership,
    DatasetRevision,
    register_instrument,
)


@pytest.mark.skipif(
    not os.getenv("STREAM_ANALYSIS_TEST_DATABASE_URL"),
    reason="PostgreSQL integration URL is not configured",
)
def test_live_analytical_input_postgres_and_optional_portable_backup(tmp_path, monkeypatch):
    url = os.environ["STREAM_ANALYSIS_TEST_DATABASE_URL"]
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    monkeypatch.setenv("STREAM_ANALYSIS_DATA_ROOT", str(tmp_path / "data"))
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(url)
    start = datetime(2026, 9, 28, 14, tzinfo=UTC)
    now = start + timedelta(minutes=62, seconds=20)
    calendar = build_oanda_uk_calendar(
        provider="oanda",
        region="UK",
        environment="live",
        account="test",
        provider_symbol="US30_USD",
    )
    feed = HistoryFeed(
        instrument_id="US30",
        provider_symbol="US30_USD",
        calendar_version=calendar.version,
        account_fingerprint=sha256(uuid4().bytes).hexdigest(),
    )
    revision_id = str(uuid4())

    def bar(minute):
        return Bar(
            "US30",
            Timeframe.M1,
            start + timedelta(minutes=minute),
            Decimal("100"),
            Decimal("103"),
            Decimal("99"),
            Decimal("101"),
            source_id="oanda-midpoint",
        )

    bars = tuple(bar(minute) for minute in range(60))
    end = start + timedelta(hours=1)
    provenance = json.dumps(
        {
            "provider": "oanda",
            "environment": "live",
            "symbol": "US30_USD",
            "source_id": "oanda-midpoint",
            "price_component": "M",
            "smooth": False,
            "account_fingerprint": feed.account_fingerprint,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    store = DatasetStore(tmp_path / "data")
    with engine.begin() as connection:
        register_instrument(connection, build_oanda_uk_instrument("US30_USD"))
        store.publish(
            connection,
            DatasetRevision(
                revision_id,
                revision_id,
                "fixture",
                "oanda",
                now,
                now,
                feed.normalization_version,
                calendar.version,
                DATASET_FORMAT_VERSION,
                (DatasetMembership("US30", Timeframe.M1, start, end, 60),),
                f"datasets/{revision_id}/manifest.json",
            ),
            DatasetLineage(
                revision_id,
                "fixture",
                "US30",
                Timeframe.M1,
                start,
                end,
                start,
                end,
                60,
                now,
                ValidationStatus.PASS,
                provenance,
                "f" * 64,
                canonical_bar_checksum_ordered(bars),
                BAR_CHECKSUM_VERSION,
                DATASET_FORMAT_VERSION,
            ),
            bars,
        )

    class Source:
        def poll_live(self, request, *, at):
            return LiveCandleUpdate(
                HistoricalDataPage(
                    request,
                    tuple(
                        bar(minute)
                        for minute in (60, 61)
                        if request.start <= bar(minute).timestamp < request.end
                    ),
                    HistoricalSource("oanda", "US30_USD", "oanda-midpoint", "live"),
                ),
                at,
                replace(bar(62), is_complete=False),
            )

        def close(self):
            pass

    hub = LiveIngress(
        engine=lambda: engine, source=lambda _: Source(), clock=lambda: now, threaded=False
    )
    manager = LiveAnalysisManager(hub)
    try:
        state = manager.connect(
            LiveAnalysisRequest(
                subscription_id=uuid4(),
                bootstrap_revision_id=revision_id,
                streams=[{"config": demo_detection_config("US30")}],
            ),
            feed,
        )
        hub.poll(feed_key(feed))
        with engine.connect() as connection:
            assert read_live_input(connection, store, state["input_id"])["source_minutes"] == 2
        manager.close()
        hub.close()
        container = os.getenv("STREAM_ANALYSIS_TEST_PG_CONTAINER")
        if container:
            # Opt in only for a fresh container owned by this validation run.
            dump = tmp_path / "postgres.dump"
            with dump.open("wb") as output:
                subprocess.run(
                    [
                        "docker",
                        "exec",
                        container,
                        "pg_dump",
                        "-U",
                        "postgres",
                        "-d",
                        engine.url.database,
                        "-Fc",
                    ],
                    stdout=output,
                    check=True,
                )
            archive = create_archive(store.root, dump, tmp_path / "backup.zip")
            target = tmp_path / "restored-data"
            target.mkdir()
            restored_dump = extract_verified_archive(archive, target, verify_archive(archive))
            destination = f"restore_{uuid4().hex}"
            subprocess.run(
                ["docker", "exec", container, "createdb", "-U", "postgres", destination], check=True
            )
            restored = create_engine(engine.url.set(database=destination))
            try:
                with restored_dump.open("rb") as source:
                    subprocess.run(
                        [
                            "docker",
                            "exec",
                            "-i",
                            container,
                            "pg_restore",
                            "-U",
                            "postgres",
                            "-d",
                            destination,
                            "--no-owner",
                            "--no-acl",
                            "--exit-on-error",
                        ],
                        stdin=source,
                        check=True,
                    )
                with restored.connect() as connection:
                    assert verify_live_retention(connection) == 2
                    assert verify_live_inputs(connection, DatasetStore(target)) == 1
                    assert (
                        read_live_input(connection, DatasetStore(target), state["input_id"])[
                            "available_at"
                        ]
                        == "2026-09-28T15:02:00Z"
                    )
            finally:
                restored.dispose()
                subprocess.run(
                    ["docker", "exec", container, "dropdb", "-U", "postgres", destination],
                    check=True,
                )
    finally:
        manager.close()
        hub.close()
        engine.dispose()
