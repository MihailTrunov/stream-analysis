"""Idempotent offline, non-research-grade US30/DAX replay revisions."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256

from sqlalchemy import Connection

from market_analysis.config.oanda_uk_calendars import (
    OANDA_UK_LIVE_PROFILES,
    build_oanda_uk_instrument,
)
from market_analysis.domain import (
    BAR_CHECKSUM_VERSION,
    Bar,
    DatasetLineage,
    Timeframe,
    ValidationStatus,
    canonical_bar_checksum,
)
from market_analysis.persistence.dataset_store import DATASET_FORMAT_VERSION, DatasetStore
from market_analysis.persistence.market_data import (
    DatasetMembership,
    DatasetRevision,
    register_instrument,
)

DEMO_START = datetime(2026, 1, 5, 12, tzinfo=UTC)
DEMO_SELECTED_START = DEMO_START + timedelta(minutes=11)
DEMO_END = DEMO_START + timedelta(minutes=14)


def demo_replay_bars(instrument_id: str) -> tuple[Bar, ...]:
    """Real compression-detector candidate in warm-up, active/release in view."""
    if instrument_id not in ("US30", "DAX"):
        raise ValueError("offline demo supports US30 and DAX only")
    base = Decimal(42_000 if instrument_id == "US30" else 16_000)
    result = []
    for index in range(14):
        close = base + (Decimal(20) if index == 13 else Decimal(10 + index % 2))
        result.append(Bar(
            instrument_id, Timeframe.M1, DEMO_START + timedelta(minutes=index),
            close, base + (Decimal(21) if index == 13 else Decimal(12)),
            base + Decimal(9), close, source_id="seeded-replay-demo",
        ))
    return tuple(result)


def seed_replay_datasets(connection: Connection, store: DatasetStore) -> None:
    """Publish both deterministic local samples without network or credentials."""
    for symbol in ("US30_USD", "DE30_EUR"):
        profile = OANDA_UK_LIVE_PROFILES[symbol]
        instrument_id = profile.instrument_id
        bars = demo_replay_bars(instrument_id)
        revision_id = f"offline-replay-{instrument_id.lower()}-v1"
        source_id = f"seeded-replay-{instrument_id.lower()}-v1"
        checksum = canonical_bar_checksum(bars)
        source_checksum = sha256(f"seeded-replay-v1:{instrument_id}".encode()).hexdigest()
        register_instrument(connection, build_oanda_uk_instrument(symbol))
        revision = DatasetRevision(
            dataset_revision_id=revision_id,
            dataset_id=f"offline-replay-{instrument_id.lower()}",
            source_id=source_id,
            provider="seeded-demo",
            retrieved_at=DEMO_START,
            created_at=DEMO_START,
            normalization_version="seeded-replay-v1",
            calendar_version=profile.version,
            manifest_format_version=DATASET_FORMAT_VERSION,
            memberships=(DatasetMembership(
                instrument_id, Timeframe.M1, DEMO_START, DEMO_END, len(bars)
            ),),
            manifest_ref=f"datasets/{revision_id}/manifest.json",
        )
        lineage = DatasetLineage(
            dataset_revision_id=revision_id,
            source_dataset_id=source_id,
            instrument_id=instrument_id,
            timeframe=Timeframe.M1,
            requested_start=DEMO_START,
            requested_end=DEMO_END,
            actual_start=DEMO_START,
            actual_end=DEMO_END,
            bar_count=len(bars),
            acquired_at=DEMO_START,
            validation_status=ValidationStatus.PASS,
            provider_request_json=json.dumps({
                "source": "offline-replay-demo",
                "non_research_grade": True,
                "calendar_id": profile.calendar_id,
                "calendar_version": profile.version,
            }, sort_keys=True, separators=(",", ":")),
            source_checksum=source_checksum,
            canonical_checksum=checksum,
            checksum_version=BAR_CHECKSUM_VERSION,
            dataset_format_version=DATASET_FORMAT_VERSION,
        )
        store.publish(connection, revision, lineage, bars)
