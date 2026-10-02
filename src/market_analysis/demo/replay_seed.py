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
DEMO_V2_START = datetime(2026, 1, 5, 9, tzinfo=UTC)
DEMO_V2_SELECTED_START = DEMO_V2_START + timedelta(hours=1)
DEMO_V2_END = DEMO_V2_START + timedelta(hours=5)


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


def multi_hour_demo_bars(instrument_id: str) -> tuple[Bar, ...]:
    """Five deterministic in-session hours with varied, continuous price action."""
    if instrument_id not in ("US30", "DAX"):
        raise ValueError("offline demo supports US30 and DAX only")
    price = 420_000 if instrument_id == "US30" else 160_000  # index tenths
    scale = 2 if instrument_id == "US30" else 1
    noise = (0, 2, -1, 1, -2, 3, -1, 0, 1, -3, 2)
    bars: list[Bar] = []
    for index in range(300):
        opening = price
        jitter = noise[index % len(noise)] * scale
        if index < 65:
            change = 4 * scale + jitter
        elif index < 115:
            change = -3 * scale + jitter
        elif index < 140:
            change = noise[(index * 3) % len(noise)] * 3 * scale
        elif index < 195:
            # A progressively narrower, direction-neutral oscillation gives
            # RangeState a causal compression interval before the release.
            amplitude = max(1, 9 - (index - 140) // 6) * scale
            change = amplitude if index % 2 == 0 else -amplitude
        elif index < 245:
            change = 6 * scale + jitter
        else:
            change = -1 * scale + jitter * 2
        price += change
        wick = (4 + (index * 7) % 5) * scale
        high = max(opening, price) + wick
        low = min(opening, price) - wick
        bars.append(Bar(
            instrument_id, Timeframe.M1, DEMO_V2_START + timedelta(minutes=index),
            Decimal(opening) / 10, Decimal(high) / 10,
            Decimal(low) / 10, Decimal(price) / 10,
            source_id="seeded-replay-demo-v2",
        ))
    return tuple(bars)


def balanced_multi_hour_demo_bars(instrument_id: str) -> tuple[Bar, ...]:
    """Five hours with directional moves and one deliberate compression episode."""
    if instrument_id not in ("US30", "DAX"):
        raise ValueError("offline demo supports US30 and DAX only")
    price = 420_000 if instrument_id == "US30" else 160_000
    scale = 2 if instrument_id == "US30" else 1
    small_noise = (-1, 0, 1, 0, 1, -1, 0)
    bars: list[Bar] = []
    for index in range(300):
        opening = price
        noise = small_noise[index % len(small_noise)] * scale
        if index < 65:
            change, wick = 7 * scale + noise, (1 + index % 2) * scale
        elif index < 115:
            change, wick = -5 * scale + noise, (1 + index % 2) * scale
        elif index < 135:
            change, wick = (12 if index % 2 == 0 else -12) * scale, 4 * scale
        elif index < 195:
            amplitude = max(1, 10 - (index - 135) // 6) * scale
            change, wick = (amplitude if index % 2 == 0 else -amplitude), 4 * scale
        elif index < 245:
            change, wick = 8 * scale + noise, (1 + index % 2) * scale
        else:
            change, wick = -4 * scale + noise, (1 + index % 2) * scale
        price += change
        high = max(opening, price) + wick
        low = min(opening, price) - wick
        bars.append(Bar(
            instrument_id, Timeframe.M1, DEMO_V2_START + timedelta(minutes=index),
            Decimal(opening) / 10, Decimal(high) / 10,
            Decimal(low) / 10, Decimal(price) / 10,
            source_id="seeded-replay-demo-v3",
        ))
    return tuple(bars)


def seed_replay_datasets(connection: Connection, store: DatasetStore) -> None:
    """Publish both deterministic local samples without network or credentials."""
    for symbol in ("US30_USD", "DE30_EUR"):
        profile = OANDA_UK_LIVE_PROFILES[symbol]
        instrument_id = profile.instrument_id
        register_instrument(connection, build_oanda_uk_instrument(symbol))
        for version, bars, start, end in (
            ("v1", demo_replay_bars(instrument_id), DEMO_START, DEMO_END),
            ("v2", multi_hour_demo_bars(instrument_id), DEMO_V2_START, DEMO_V2_END),
            ("v3", balanced_multi_hour_demo_bars(instrument_id), DEMO_V2_START, DEMO_V2_END),
        ):
            revision_id = f"offline-replay-{instrument_id.lower()}-{version}"
            source_id = f"seeded-replay-{instrument_id.lower()}-{version}"
            checksum = canonical_bar_checksum(bars)
            source_checksum = sha256(
                f"seeded-replay-{version}:{instrument_id}".encode()
            ).hexdigest()
            revision = DatasetRevision(
                dataset_revision_id=revision_id,
                dataset_id=f"offline-replay-{instrument_id.lower()}",
                source_id=source_id,
                provider="seeded-demo",
                retrieved_at=start,
                created_at=start,
                normalization_version=f"seeded-replay-{version}",
                calendar_version=profile.version,
                manifest_format_version=DATASET_FORMAT_VERSION,
                memberships=(DatasetMembership(
                    instrument_id, Timeframe.M1, start, end, len(bars)
                ),),
                manifest_ref=f"datasets/{revision_id}/manifest.json",
            )
            lineage = DatasetLineage(
                dataset_revision_id=revision_id,
                source_dataset_id=source_id,
                instrument_id=instrument_id,
                timeframe=Timeframe.M1,
                requested_start=start,
                requested_end=end,
                actual_start=start,
                actual_end=end,
                bar_count=len(bars),
                acquired_at=start,
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
