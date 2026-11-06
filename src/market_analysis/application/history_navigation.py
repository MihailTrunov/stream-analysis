"""Calendar-aware source preparation for chart navigation and fresh replay entry."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import Connection

from market_analysis.config import resolve_detection_config
from market_analysis.config.oanda_uk_calendars import OANDA_UK_LIVE_PROFILES
from market_analysis.domain import Timeframe, TradingCalendar
from market_analysis.domain.aggregation import bucket_bounds
from market_analysis.persistence.history_ranges import HistoryRange
from market_analysis.persistence.market_data import load_dataset_lineage, load_dataset_revision

from .analysis_sessions import StreamSettings, preview_configuration
from .history_prices import completed_buckets
from .history_ranges import HistoryFeed, request_history, stamp
from .replay_catalog import PATTERN_DEFINITIONS, required_warmup_bars


def _preceding(
    calendar: TradingCalendar,
    timeframe: Timeframe,
    first: datetime,
    end: datetime,
    count: int,
    *,
    required: bool,
) -> datetime:
    found, start = 0, first
    for left, _ in completed_buckets(calendar, timeframe, first, end):
        start = left
        found += 1
        if found >= count:
            break
    if required and found < count:
        raise ValueError(f"insufficient verified {timeframe.value} initialization history")
    return start


def plan_history_navigation(
    connection: Connection,
    feed: HistoryFeed,
    source_revision_id: str | None,
    target: datetime,
    timeframe: Timeframe,
    streams: tuple[StreamSettings, ...],
    intent: Literal["move", "replay"],
    *,
    at: datetime,
    end: datetime | None = None,
) -> dict[str, Any]:
    if target.tzinfo is None or target.second or target.microsecond:
        raise ValueError("navigation target must be an aware completed minute")
    target = target.astimezone(UTC)
    if source_revision_id is not None:
        revision = load_dataset_revision(connection, source_revision_id)
        lineage = load_dataset_lineage(
            connection, source_revision_id, feed.instrument_id, Timeframe.M1,
        )
        if revision is None or lineage is None:
            raise ValueError("selected source not found")
        provenance = json.loads(lineage.provider_request_json)
        if revision.provider in {"history-composite", "live-capture"}:
            selected_feed = HistoryFeed.model_validate(provenance["feed"])
        elif revision.provider == "oanda":
            selected_feed = HistoryFeed(
                instrument_id=feed.instrument_id,
                calendar_version=revision.calendar_version,
                normalization_version=revision.normalization_version,
                provider=provenance["provider"], environment=provenance["environment"],
                provider_symbol=provenance["symbol"], source_id=provenance["source_id"],
                price_component=provenance["price_component"], smooth=provenance["smooth"],
                account_fingerprint=provenance["account_fingerprint"],
            )
        else:
            raise ValueError("synthetic or unsupported sources cannot acquire provider history")
        if selected_feed != feed:
            raise ValueError(
                "selected source differs from the configured provider/account/calendar feed"
            )
    calendar = feed.calendar()
    calendar.derive(target)
    profile = OANDA_UK_LIVE_PROFILES[feed.provider_symbol]
    zone = ZoneInfo(profile.timezone_name)
    verified_start = datetime.combine(
        profile.coverage_start, profile.trading_day_boundary, zone
    ).astimezone(UTC)
    verified_end = datetime.combine(
        profile.coverage_end + timedelta(days=1), profile.trading_day_boundary, zone
    ).astimezone(UTC)
    latest = min(at.astimezone(UTC).replace(second=0, microsecond=0), verified_end)
    if target > latest:
        raise ValueError("navigation target is later than completed verified history")
    if len({stream.config.timeframe for stream in streams}) != len(streams):
        raise ValueError("select each analytical timeframe once")
    if intent == "replay":
        if not streams:
            raise ValueError("apply detector settings before starting replay")
        if end is not None and (end.tzinfo is None or end.second or end.microsecond):
            raise ValueError("replay end must be an aware minute")
        finish = end.astimezone(UTC) if end is not None else latest
        if not target < finish <= latest:
            raise ValueError(
                "choose a replay end after the start and within completed verified history"
            )
        first = _preceding(calendar, timeframe, verified_start, target, 90, required=False)
        initialization_end = target
    else:
        finish = min(bucket_bounds(target, timeframe, calendar)[1], latest)
        first = _preceding(calendar, timeframe, verified_start, finish, 500, required=False)
        initialization_end = first
    initialization = "available"
    for stream in streams:
        if (
            stream.config.instrument_id != feed.instrument_id
            or stream.config.calendar_id != calendar.calendar_id
        ):
            raise ValueError("detector settings differ from the selected instrument/calendar")
        preview_configuration(stream.config)
        if not any(pattern.enabled for pattern in stream.config.patterns):
            continue
        resolved = resolve_detection_config(stream.config, pattern_definitions=PATTERN_DEFINITIONS)
        required = required_warmup_bars(resolved)
        if required:
            try:
                first = min(
                    first,
                    _preceding(
                        calendar,
                        resolved.timeframe,
                        verified_start,
                        initialization_end,
                        required,
                        required=True,
                    ),
                )
            except ValueError:
                if intent == "replay":
                    raise
                first = verified_start
                initialization = "unavailable"
    if first >= finish:
        raise ValueError("no completed history is available for this target")
    if finish - first > timedelta(days=31 * 32):
        raise ValueError("requested period is too large; choose an earlier replay end")
    requests: list[HistoryRange] = []
    cursor = first
    while cursor < finish:
        next_end = min(cursor + timedelta(days=31), finish)
        requests.append(request_history(connection, feed, cursor, next_end, at=at))
        cursor = next_end
    return {
        "request_ids": [request.request_id for request in requests],
        "instrument_id": feed.instrument_id,
        "target": stamp(target),
        "start": stamp(first),
        "end": stamp(finish),
        "timeframe": timeframe.value,
        "intent": intent,
        "initialization": initialization,
    }
