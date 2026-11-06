"""Resolve and verify live input manifests and their append-only confirmed prefix."""

import json
from datetime import datetime
from hashlib import sha256
from typing import Any

from sqlalchemy import Connection, func, select

from market_analysis.domain import Timeframe
from market_analysis.domain.aggregation import MINUTE
from market_analysis.persistence.dataset_store import DatasetStore
from market_analysis.persistence.history_ranges import utc
from market_analysis.persistence.live_analysis import (
    live_analysis_advances,
    live_analysis_closes,
    live_analysis_inputs,
)
from market_analysis.persistence.live_candles import live_candles
from market_analysis.persistence.market_data import load_dataset_lineage

from .analysis_clock import verify_closed_interval
from .history_ranges import HistoryFeed, stamp
from .live_retention import _decode, feed_key


def read_live_input(connection: Connection, store: DatasetStore, input_id: str) -> dict[str, Any]:
    row = connection.execute(select(live_analysis_inputs).where(
        live_analysis_inputs.c.input_id == input_id,
    )).mappings().one_or_none()
    if row is None:
        raise ValueError("live input manifest is unavailable")
    if sha256(row["manifest_json"].encode()).hexdigest() != row["manifest_hash"]:
        raise ValueError("live input manifest checksum differs")
    manifest = json.loads(row["manifest_json"])
    if manifest["version"] != "live-analysis-input-v1":
        raise ValueError("unsupported live input version")
    feed = HistoryFeed.model_validate(manifest["feed"])
    calendar = feed.calendar()
    lineage = load_dataset_lineage(connection, manifest["bootstrap_revision_id"],
                                   feed.instrument_id, Timeframe.M1)
    if lineage is None or lineage.canonical_checksum != manifest["bootstrap_checksum"]:
        raise ValueError("live initialization source lineage differs")
    store.read_range(connection, manifest["bootstrap_revision_id"], feed.instrument_id,
                     Timeframe.M1, lineage.requested_start, lineage.requested_end, limit=1)
    start = datetime.fromisoformat(manifest["live_start"])
    latest = connection.scalar(select(func.max(live_analysis_advances.c.available_at)).where(
        live_analysis_advances.c.input_id == input_id,
    ))
    watermark = utc(latest) if latest is not None else start
    if (start != lineage.requested_end or watermark < start
        or start.second or start.microsecond or watermark.second or watermark.microsecond):
        raise ValueError("live input availability differs from its bootstrap boundary")
    # Freeze the upper edge before reading receipts so active ingestion cannot
    # turn this read into an inconsistent combination of two different prefixes.
    receipts = connection.execute(select(
        live_analysis_closes.c.timestamp, live_analysis_closes.c.feed_key,
        live_analysis_closes.c.sha256, live_candles.c.sha256.label("journal_sha256"),
        live_candles.c.bar_json,
    ).outerjoin(live_candles,
                (live_candles.c.feed_key == live_analysis_closes.c.feed_key)
                & (live_candles.c.timestamp == live_analysis_closes.c.timestamp)).where(
        live_analysis_closes.c.input_id == input_id,
        live_analysis_closes.c.timestamp < watermark,
    ).order_by(live_analysis_closes.c.timestamp).execution_options(yield_per=500)).mappings()
    cursor, count = start, 0
    for receipt in receipts:
        timestamp = utc(receipt["timestamp"])
        if (receipt["feed_key"] != feed_key(feed)
            or receipt["sha256"] != receipt["journal_sha256"]
            or timestamp < cursor or timestamp + MINUTE > watermark):
            raise ValueError("live analytical receipt differs from retained source")
        verify_closed_interval(calendar, cursor, timestamp)
        bar = _decode(receipt)
        if bar.instrument_id != feed.instrument_id or bar.source_id != feed.source_id:
            raise ValueError("live receipt belongs to a different source feed")
        cursor = timestamp + MINUTE
        count += 1
    verify_closed_interval(calendar, cursor, watermark)
    return {"input_id": input_id, "session_id": row["session_id"],
            "manifest": manifest, "manifest_hash": row["manifest_hash"],
            "available_at": stamp(watermark), "source_minutes": count}


def verify_live_inputs(connection: Connection, store: DatasetStore) -> int:
    count = 0
    for input_id in connection.scalars(select(live_analysis_inputs.c.input_id)):
        result = read_live_input(connection, store, input_id)
        count_all = connection.scalar(select(func.count()).select_from(live_analysis_closes).where(
            live_analysis_closes.c.input_id == input_id,
        ))
        if count_all != result["source_minutes"]:
            raise ValueError("live input contains receipts beyond its committed watermark")
        count += 1
    return count
