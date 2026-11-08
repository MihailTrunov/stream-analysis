"""Durability-before-acknowledgment and hourly publication of canonical live minutes.

Provider streaming/finalization belongs to SCRUM-137. This boundary accepts only
already finalized canonical minutes and commits before returning to its caller.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, cast

from sqlalchemy import Connection, Engine, and_, or_, select, update
from sqlalchemy.exc import IntegrityError

from market_analysis.domain import Bar, DatasetLineage, Timeframe, ValidationStatus
from market_analysis.domain.dataset_lineage import (
    BAR_CHECKSUM_VERSION,
    canonical_bar_checksum_ordered,
)
from market_analysis.persistence.dataset_store import DATASET_FORMAT_VERSION, DatasetStore
from market_analysis.persistence.history_ranges import utc
from market_analysis.persistence.live_candles import live_candles, live_feeds, live_segments
from market_analysis.persistence.market_data import (
    DatasetMembership,
    DatasetRevision,
    load_dataset_lineage,
    load_instrument,
)

from .history_ranges import HistoryFeed, stamp

MINUTE = timedelta(minutes=1)
HOUR = timedelta(hours=1)


class LiveRetentionConflict(ValueError):
    """A finalized minute conflicts with retained content or a sealed hour."""


def feed_key(feed: HistoryFeed) -> str:
    return sha256(feed.canonical_json.encode()).hexdigest()


def _aware(at: datetime) -> datetime:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("live retention requires an aware observation time")
    return at.astimezone(UTC)


def _lock_feed(connection: Connection, feed: HistoryFeed) -> str:
    key = feed_key(feed)
    try:
        with connection.begin_nested():
            connection.execute(live_feeds.insert().values(
                feed_key=key, feed_json=feed.canonical_json,
            ))
    except IntegrityError:
        pass
    value = connection.scalar(
        select(live_feeds.c.feed_json).where(live_feeds.c.feed_key == key).with_for_update()
    )
    if value != feed.canonical_json:
        raise LiveRetentionConflict("retained feed identity differs")
    return key


def _decode(row: Any) -> Bar:
    encoded = row["bar_json"]
    if sha256(encoded.encode()).hexdigest() != row["sha256"]:
        raise LiveRetentionConflict("retained minute checksum differs")
    bar = Bar.from_canonical_dict(json.loads(encoded))
    if bar.timestamp != utc(row["timestamp"]):
        raise LiveRetentionConflict("retained minute identity differs")
    return bar


def retain_completed_minute(
    engine: Engine, feed: HistoryFeed, bar: Bar, *, at: datetime,
) -> dict[str, object]:
    """Return only after commit. Exact redelivery is idempotent; corrections fail closed."""
    at = _aware(at)
    calendar = feed.calendar()
    if (
        bar.instrument_id != feed.instrument_id or bar.source_id != feed.source_id
        or bar.timeframe is not Timeframe.M1 or not bar.is_complete
        or bar.timestamp.second or bar.timestamp.microsecond or bar.timestamp + MINUTE > at
        or not calendar.derive(bar.timestamp).is_open
    ):
        raise ValueError("live retention accepts only finalized open-calendar canonical M1 bars")
    encoded = json.dumps(dict(bar.to_canonical_dict()), sort_keys=True, separators=(",", ":"))
    digest = sha256(encoded.encode()).hexdigest()
    hour = bar.timestamp.replace(minute=0)
    with engine.begin() as connection:
        if load_instrument(connection, feed.instrument_id) is None:
            raise ValueError("live retention instrument is not registered")
        key = _lock_feed(connection, feed)
        existing = connection.execute(select(live_candles).where(
            live_candles.c.feed_key == key, live_candles.c.timestamp == bar.timestamp,
        )).mappings().one_or_none()
        if existing is not None:
            if _decode(existing) != bar:
                raise LiveRetentionConflict("completed minute conflicts with retained content")
            duplicate = True
        else:
            sealed = connection.scalar(select(live_segments.c.revision_id).where(
                live_segments.c.feed_key == key, live_segments.c.hour_start == hour,
            ))
            if sealed is not None:
                raise LiveRetentionConflict(
                    "hour is sealed; publish a separate reviewed correction"
                )
            connection.execute(live_candles.insert().values(
                feed_key=key, timestamp=bar.timestamp, hour_start=hour,
                bar_json=encoded, sha256=digest, recorded_at=at,
            ))
            marker = connection.execute(select(live_segments.c.hour_start).where(
                live_segments.c.feed_key == key, live_segments.c.hour_start == hour,
            )).first()
            if marker is None:
                connection.execute(live_segments.insert().values(feed_key=key, hour_start=hour))
            duplicate = False
    return {"feed_key": key, "timestamp": stamp(bar.timestamp), "duplicate": duplicate}


def read_live_history(
    connection: Connection, key: str, start: datetime, end: datetime, *, at: datetime,
    limit: int = 500,
) -> dict[str, object]:
    """Read the durable journal before or after packaging; never invent missing minutes."""
    start, end, at = _aware(start), _aware(end), _aware(at)
    if (start >= end or end - start > timedelta(days=31) or end > at
        or start.second or start.microsecond or end.second or end.microsecond
        or isinstance(limit, bool) or not 1 <= limit <= 500):
        raise ValueError("live history needs aligned bounds (at most 31 days), limit 1..500")
    encoded = connection.scalar(select(live_feeds.c.feed_json).where(live_feeds.c.feed_key == key))
    if encoded is None:
        raise ValueError("live feed is not retained")
    feed = HistoryFeed.model_validate_json(encoded)
    calendar = feed.calendar()
    rows = connection.execute(select(live_candles).where(
        live_candles.c.feed_key == key, live_candles.c.timestamp >= start,
        live_candles.c.timestamp < end,
    ).order_by(live_candles.c.timestamp.desc()).limit(limit)).mappings().all()
    bars = [_decode(row) for row in reversed(rows)]
    # Coverage refers to this bounded displayed slice; earlier pages remain separate.
    left = bars[0].timestamp if len(bars) == limit else start
    present = {bar.timestamp for bar in bars}
    missing = []
    cursor = left
    while cursor < end:
        if calendar.derive(cursor).is_open and cursor not in present:
            missing.append(stamp(cursor))
        cursor += MINUTE
    return {
        "feed_key": key, "start": stamp(left), "end": stamp(end),
        "status": "gap" if missing else "available", "missing_minutes": missing,
        "bars": [dict(bar.to_canonical_dict()) for bar in bars],
    }


def package_live_hour(
    engine: Engine, store: DatasetStore, feed: HistoryFeed, hour: datetime, *, at: datetime,
) -> str | None:
    """Seal one elapsed hour atomically. Gaps block publication until repaired.

    Capture may start mid-hour; coverage starts at its first retained minute.
    Retained rows are never deleted, including after publication or publication failure.
    """
    hour, at = _aware(hour), _aware(at)
    if hour.minute or hour.second or hour.microsecond or hour + HOUR > at:
        raise ValueError("only elapsed aligned UTC hours can be packaged")
    calendar = feed.calendar()
    with engine.begin() as connection:
        key = _lock_feed(connection, feed)
        sealed = connection.scalar(select(live_segments.c.revision_id).where(
            live_segments.c.feed_key == key, live_segments.c.hour_start == hour,
        ))
        if sealed is not None:
            # Reauthenticate the publication before reporting successful recovery.
            store.read_range(connection, sealed, feed.instrument_id, Timeframe.M1,
                             hour, hour + HOUR, limit=1)
            return cast(str, sealed)
        rows = connection.execute(select(live_candles).where(
            live_candles.c.feed_key == key, live_candles.c.hour_start == hour,
        ).order_by(live_candles.c.timestamp)).mappings().all()
        if not rows:
            return None
        if calendar.observed_only:
            return None  # Durable receipts remain available; do not publish a verified hour.
        connection.execute(update(live_segments).where(
            live_segments.c.feed_key == key, live_segments.c.hour_start == hour,
        ).values(attempted_at=at))
        bars = tuple(_decode(row) for row in rows)
        start, end = bars[0].timestamp, hour + HOUR
        present = {bar.timestamp for bar in bars}
        cursor = start
        while cursor < end:
            if calendar.derive(cursor).is_open and cursor not in present:
                return None
            cursor += MINUTE
        acquired = max(utc(row["recorded_at"]) for row in rows)
        checksum = canonical_bar_checksum_ordered(bars)
        revision_id = f"live-{key}-{hour.strftime('%Y%m%dT%H')}-{checksum[:16]}"
        source_checksum = sha256("\n".join(row["sha256"] for row in rows).encode()).hexdigest()
        provenance = {
            "provider": feed.provider, "environment": feed.environment,
            "symbol": feed.provider_symbol, "source_id": feed.source_id,
            "account_fingerprint": feed.account_fingerprint,
            "price_component": feed.price_component,
            "smooth": feed.smooth, "capture": "live-journal-v1",
            "gap_report": {"version": "calendar-gaps-v1", "missing_intervals": [],
                           "calendar_version": feed.calendar_version, "unexpected_intervals": []},
        }
        revision = DatasetRevision(
            revision_id, f"live-{key}", key, feed.provider, acquired, end,
            feed.normalization_version, feed.calendar_version, DATASET_FORMAT_VERSION,
            (DatasetMembership(feed.instrument_id, Timeframe.M1, start, end, len(bars)),),
            f"datasets/{revision_id}/manifest.json",
        )
        lineage = DatasetLineage(
            revision_id, key, feed.instrument_id, Timeframe.M1, start, end,
            start, bars[-1].timestamp + MINUTE, len(bars), acquired, ValidationStatus.PASS,
            json.dumps(provenance, sort_keys=True, separators=(",", ":")),
            source_checksum, checksum, BAR_CHECKSUM_VERSION, DATASET_FORMAT_VERSION,
        )
        store.publish(connection, revision, lineage, bars)
        connection.execute(update(live_segments).where(
            live_segments.c.feed_key == key, live_segments.c.hour_start == hour,
        ).values(revision_id=revision_id))
        return revision_id


def package_pending_live_hours(
    engine: Engine, store: DatasetStore, *, at: datetime, limit: int = 4,
) -> list[str]:
    """Bound each worker iteration; continue past blocked hours to other feed/hour pairs."""
    at = _aware(at)
    if not 1 <= limit <= 32:
        raise ValueError("packaging budget must be 1..32 hours")
    with engine.connect() as connection:
        pairs = connection.execute(select(
            live_segments.c.feed_key, live_segments.c.hour_start, live_feeds.c.feed_json,
        ).join(live_feeds, live_feeds.c.feed_key == live_segments.c.feed_key).where(
            live_segments.c.revision_id.is_(None),
            live_segments.c.hour_start <= at - HOUR,
        ).order_by(live_segments.c.attempted_at.asc().nulls_first(),
                   live_segments.c.hour_start, live_segments.c.feed_key).limit(limit)).all()
    published = []
    # Attempt timestamps rotate blocked hours behind unattempted/later work.
    for key, hour, encoded in pairs:
        # Persist scheduling fairness even if file publication raises/rolls back.
        with engine.begin() as connection:
            connection.execute(update(live_segments).where(
                live_segments.c.feed_key == key, live_segments.c.hour_start == hour,
            ).values(attempted_at=at))
        result = package_live_hour(engine, store, HistoryFeed.model_validate_json(encoded),
                                   utc(hour), at=at)
        if result is not None:
            published.append(result)
    return published


def verify_live_retention(connection: Connection) -> int:
    """Authenticate journal content and sealed ownership during portable restore."""
    orphan = connection.execute(select(live_candles.c.timestamp).outerjoin(
        live_feeds, live_feeds.c.feed_key == live_candles.c.feed_key,
    ).outerjoin(live_segments, and_(
        live_segments.c.feed_key == live_candles.c.feed_key,
        live_segments.c.hour_start == live_candles.c.hour_start,
    )).where(or_(live_feeds.c.feed_key.is_(None), live_segments.c.feed_key.is_(None)))
      .limit(1)).first()
    if orphan is not None:
        raise LiveRetentionConflict("retained minute has no feed/hour metadata")
    count = 0
    for feed_row in connection.execute(select(live_feeds)).mappings():
        feed = HistoryFeed.model_validate_json(feed_row["feed_json"])
        key = feed_key(feed)
        if key != feed_row["feed_key"]:
            raise LiveRetentionConflict("retained feed checksum differs")
        calendar = feed.calendar()
        for row in connection.execute(select(live_candles).where(
            live_candles.c.feed_key == key,
        ).order_by(live_candles.c.timestamp).execution_options(yield_per=500)).mappings():
            bar = _decode(row)
            if (bar.instrument_id != feed.instrument_id or bar.source_id != feed.source_id
                or bar.timeframe is not Timeframe.M1 or not bar.is_complete
                or bar.timestamp.second or bar.timestamp.microsecond
                or bar.timestamp.replace(minute=0) != utc(row["hour_start"])
                or bar.timestamp + MINUTE > utc(row["recorded_at"])
                or not calendar.derive(bar.timestamp).is_open):
                raise LiveRetentionConflict("retained minute validation/provenance differs")
            count += 1
        for marker in connection.execute(select(live_segments).where(
            live_segments.c.feed_key == key,
        )).mappings().yield_per(500):
            rows = connection.execute(select(live_candles).where(
                live_candles.c.feed_key == key,
                live_candles.c.hour_start == marker["hour_start"],
            ).order_by(live_candles.c.timestamp)).mappings().all()
            if not rows:
                raise LiveRetentionConflict("hour marker has no retained minutes")
            if marker["revision_id"] is None:
                continue
            lineage = load_dataset_lineage(connection, marker["revision_id"],
                                           feed.instrument_id, Timeframe.M1)
            if (lineage is None or lineage.source_dataset_id != key
                or lineage.canonical_checksum != canonical_bar_checksum_ordered(
                    _decode(row) for row in rows
                )):
                raise LiveRetentionConflict("sealed hour differs from retained journal")
    return count
