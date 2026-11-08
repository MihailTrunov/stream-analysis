"""Immutable provider-candle inputs for analysis without confirmed session coverage."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from sqlalchemy import Engine

from market_analysis.config.oanda_uk_calendars import build_oanda_uk_instrument
from market_analysis.domain import DatasetLineage, Timeframe, ValidationStatus
from market_analysis.domain.aggregation import MINUTE
from market_analysis.domain.dataset_lineage import (
    BAR_CHECKSUM_VERSION,
    canonical_bar_checksum_ordered,
)
from market_analysis.domain.session_calendar import OBSERVED_CALENDAR_PREFIX
from market_analysis.persistence.dataset_store import DATASET_FORMAT_VERSION
from market_analysis.persistence.market_data import (
    DatasetMembership,
    DatasetRevision,
    register_instrument,
)

from .browser_replay import _store
from .history_ranges import HistoryFeed, stamp
from .provider_prices import ProviderPrices, price_key


def observed_feed(feed: HistoryFeed) -> HistoryFeed:
    return feed if feed.calendar_version.startswith(OBSERVED_CALENDAR_PREFIX) else feed.model_copy(
        update={"calendar_version": OBSERVED_CALENDAR_PREFIX + feed.calendar_version})


def publish_provider_source(engine: Engine, prices: ProviderPrices, feed: HistoryFeed,
                            end: datetime | None = None, *, live: bool = False
                            ) -> dict[str, Any]:
    channel = prices.channel(feed)
    cutoff = end or prices.clock().astimezone(UTC).replace(second=0, microsecond=0)
    if (cutoff.tzinfo is None or cutoff.second or cutoff.microsecond
        or cutoff > prices.clock().astimezone(UTC).replace(second=0, microsecond=0)):
        raise ValueError('Provider analysis requires an elapsed, aligned end.')
    with channel.lock:
        bars = channel.store.read(cutoff) if end is not None else ()
    if not bars:
        prices.window(feed, Timeframe.M1, end)
        with channel.lock:
            bars = channel.store.read(cutoff)
    if not bars:
        raise ValueError("No completed provider candles are available for detectors.")
    feed = observed_feed(feed)
    # A live seed extends to the current acquisition boundary, even across closures.
    finish = cutoff if live else bars[-1].timestamp + MINUTE
    start = bars[0].timestamp
    checksum = canonical_bar_checksum_ordered(bars)
    identity = sha256(f"{feed.canonical_json}:{stamp(start)}:{stamp(finish)}:{checksum}".encode())
    revision_id = f"provider-observed-{identity.hexdigest()}"
    source_id = price_key(feed)
    now = prices.clock().astimezone(UTC)
    unknown = []
    cursor = start
    for bar in bars:
        if bar.timestamp > cursor:
            unknown.append({'start': stamp(cursor), 'end': stamp(bar.timestamp)})
        cursor = bar.timestamp + MINUTE
    if cursor < finish:
        unknown.append({'start': stamp(cursor), 'end': stamp(finish)})
    provenance = {**feed.model_dump(), 'symbol': feed.provider_symbol,
                  'calendar_validation': 'provider_observed',
                  'unclassified_intervals': unknown,
                  'capture': 'provider-candles-v1'}
    revision = DatasetRevision(
        revision_id, revision_id, source_id, 'oanda', now, now,
        feed.normalization_version, feed.calendar_version, DATASET_FORMAT_VERSION,
        (DatasetMembership(feed.instrument_id, Timeframe.M1, start, finish, len(bars)),),
        f'datasets/{revision_id}/manifest.json',
    )
    lineage = DatasetLineage(
        revision_id, source_id, feed.instrument_id, Timeframe.M1, start, finish,
        start, bars[-1].timestamp + MINUTE, len(bars), now, ValidationStatus.WARNING,
        json.dumps(provenance, sort_keys=True, separators=(',', ':')),
        checksum, checksum, BAR_CHECKSUM_VERSION, DATASET_FORMAT_VERSION,
    )
    store = _store()
    with engine.begin() as connection:
        register_instrument(connection, build_oanda_uk_instrument(feed.provider_symbol))
        from market_analysis.persistence.market_data import load_dataset_revision
        if load_dataset_revision(connection, revision_id) is None:
            store.publish(connection, revision, lineage, bars)
        else:
            store.read_range(connection, revision_id, feed.instrument_id, Timeframe.M1,
                             start, finish, limit=1)
    return {'dataset_revision_id': revision_id, 'source_dataset_id': source_id,
            'instrument_id': feed.instrument_id, 'timeframe': '1m',
            'range_start': stamp(start), 'range_end': stamp(finish),
            'bar_count': len(bars), 'canonical_checksum': checksum,
            'calendar_version': feed.calendar_version, 'calendar_status': 'unverified',
            'unclassified_intervals': unknown, 'non_research_grade': False,
            'suggested_start': stamp(bars[-1].timestamp), 'suggested_end': stamp(finish),
            'config_id': '', 'config_version': ''}
