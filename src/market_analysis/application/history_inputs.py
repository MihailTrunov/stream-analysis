"""Publish self-contained immutable replay inputs from acquired history ranges."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterator, Mapping
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import Connection, select

from market_analysis.domain import Bar, DatasetLineage, Timeframe, TradingCalendar, ValidationStatus
from market_analysis.domain.dataset_lineage import (
    BAR_CHECKSUM_VERSION,
    canonical_bar_checksum_ordered,
)
from market_analysis.persistence.dataset_store import DATASET_FORMAT_VERSION, DatasetStore
from market_analysis.persistence.history_ranges import history_range_requests, utc
from market_analysis.persistence.market_data import (
    DatasetMembership,
    DatasetRevision,
    load_dataset_lineage,
    load_dataset_revision,
)

from .history_ranges import HistoryFeed, history_coverage, stamp

COMPOSITION_VERSION = "history-input-v1"
MINUTE = timedelta(minutes=1)


class HistoryInputConflict(ValueError):
    """Reviewed history is stale, incomplete, or internally conflicting."""


def _parent_record(revision: DatasetRevision, lineage: DatasetLineage) -> dict[str, str]:
    return {
        "revision_id": revision.dataset_revision_id,
        "provider": revision.provider,
        "calendar_version": revision.calendar_version,
        "normalization_version": revision.normalization_version,
        "source_dataset_id": lineage.source_dataset_id,
        "canonical_checksum": lineage.canonical_checksum,
        "source_checksum": lineage.source_checksum,
        "checksum_version": lineage.checksum_version,
        "dataset_format_version": lineage.dataset_format_version,
    }


def _composition_plan(
    connection: Connection,
    store: DatasetStore,
    requests: Mapping[str, str],
) -> tuple[dict[str, Any], datetime, datetime]:
    if not 1 <= len(requests) <= 32:
        raise ValueError("composition requires 1..32 reviewed history requests")
    coverages = []
    created = []
    for request_id, expected_hash in sorted(requests.items()):
        coverage = history_coverage(connection, store, request_id)
        if coverage["status"] != "available":
            raise HistoryInputConflict("all history requests must be available before publication")
        if coverage["source_manifest_hash"] != expected_hash:
            raise HistoryInputConflict("history coverage changed; reload its source manifest")
        coverages.append(coverage)
        requested_at = connection.scalar(
            select(history_range_requests.c.created_at).where(
                history_range_requests.c.request_id == request_id,
            )
        )
        if requested_at is None:
            raise HistoryInputConflict("history request is no longer available")
        created.append(utc(requested_at))
    feed_jsons = {json.dumps(c["source_manifest"]["feed"], sort_keys=True) for c in coverages}
    if len(feed_jsons) != 1:
        raise ValueError(
            "composition requires the same provider/account/price/calendar/normalization feed"
        )
    feed = HistoryFeed.model_validate(coverages[0]["source_manifest"]["feed"])
    start = min(datetime.fromisoformat(c["start"]) for c in coverages)
    end = max(datetime.fromisoformat(c["end"]) for c in coverages)
    events: dict[datetime, list[tuple[str, int]]] = defaultdict(list)
    parents: dict[str, dict[str, Any]] = {}
    acquired = []
    for coverage in coverages:
        for span in coverage["source_manifest"]["sources"]:
            revision_id = span["revision_id"]
            if revision_id not in parents:
                revision = load_dataset_revision(connection, revision_id)
                lineage = load_dataset_lineage(
                    connection, revision_id, feed.instrument_id, Timeframe.M1
                )
                if revision is None or lineage is None:
                    raise HistoryInputConflict("parent revision is no longer available")
                if lineage.canonical_checksum != span["canonical_checksum"]:
                    raise HistoryInputConflict("parent checksum differs from reviewed history")
                parents[revision_id] = _parent_record(revision, lineage)
                acquired.append(lineage.acquired_at)
            if parents[revision_id]["canonical_checksum"] != span["canonical_checksum"]:
                raise HistoryInputConflict("parent checksum differs between reviewed requests")
            events[datetime.fromisoformat(span["start"])].append((revision_id, 1))
            events[datetime.fromisoformat(span["end"])].append((revision_id, -1))
    if not parents:
        raise ValueError("a closed-only history window has no replay input candles")
    boundaries = sorted({start, end, *events})
    active: dict[str, int] = {}
    sources: list[dict[str, str]] = []
    for left, right in zip(boundaries, boundaries[1:], strict=False):
        for revision_id, change in events[left]:
            count = active.get(revision_id, 0) + change
            if count:
                active[revision_id] = count
            else:
                active.pop(revision_id, None)
        if len(active) > 1:
            raise HistoryInputConflict("overlapping requests pin conflicting parent revisions")
        if active:
            revision_id = next(iter(active))
            if (
                sources
                and sources[-1]["revision_id"] == revision_id
                and sources[-1]["end"] == stamp(left)
            ):
                sources[-1]["end"] = stamp(right)
            else:
                sources.append(
                    {"start": stamp(left), "end": stamp(right), "revision_id": revision_id}
                )
    plan = {
        "version": COMPOSITION_VERSION,
        "feed": feed.model_dump(),
        "start": stamp(start),
        "end": stamp(end),
        "requests": [
            {"request_id": c["request_id"], "source_manifest_hash": c["source_manifest_hash"]}
            for c in coverages
        ],
        "parents": [parents[key] for key in sorted(parents)],
        "sources": sources,
    }
    return plan, max(created), max(acquired)


def _input_bars(
    connection: Connection,
    store: DatasetStore,
    plan: Mapping[str, Any],
    calendar: TradingCalendar,
) -> Iterator[Bar]:
    """Validate every open minute independently of the acquisition gap reports."""
    slot, end = datetime.fromisoformat(plan["start"]), datetime.fromisoformat(plan["end"])
    parents = {parent["revision_id"]: parent for parent in plan["parents"]}
    for span in plan["sources"]:
        revision = load_dataset_revision(connection, span["revision_id"])
        lineage = load_dataset_lineage(
            connection,
            span["revision_id"],
            calendar.instrument_id,
            Timeframe.M1,
        )
        if (
            revision is None
            or lineage is None
            or _parent_record(revision, lineage) != parents[span["revision_id"]]
        ):
            raise HistoryInputConflict("parent lineage changed during composition")
        for bar in store.iter_verified_range(
            connection,
            span["revision_id"],
            calendar.instrument_id,
            Timeframe.M1,
            datetime.fromisoformat(span["start"]),
            datetime.fromisoformat(span["end"]),
        ):
            while slot < bar.timestamp:
                if calendar.derive(slot).is_open:
                    raise HistoryInputConflict(f"missing expected-open source minute {stamp(slot)}")
                slot += MINUTE
            if (
                bar.timestamp != slot
                or not calendar.derive(slot).is_open
                or bar.timestamp.second
                or bar.timestamp.microsecond
                or not bar.is_complete
            ):
                raise HistoryInputConflict(
                    "composition has unordered, incomplete or closed-session input"
                )
            yield bar
            slot += MINUTE
    while slot < end:
        if calendar.derive(slot).is_open:
            raise HistoryInputConflict(f"missing expected-open source minute {stamp(slot)}")
        slot += MINUTE


def read_history_input(
    connection: Connection,
    store: DatasetStore,
    revision_id: str,
) -> dict[str, Any]:
    revision = load_dataset_revision(connection, revision_id)
    if revision is None or revision.provider != "history-composite":
        raise ValueError("published history input not found")
    member = revision.memberships[0]
    lineage = load_dataset_lineage(connection, revision_id, member.instrument_id, Timeframe.M1)
    if lineage is None:
        raise ValueError("published history input lineage is unavailable")
    manifest = json.loads(lineage.provider_request_json)
    if (
        manifest.get("version") != COMPOSITION_VERSION
        or sha256(lineage.provider_request_json.encode()).hexdigest() != lineage.source_checksum
    ):
        raise ValueError("published composition manifest checksum/version differs")
    store.read_range(
        connection,
        revision_id,
        member.instrument_id,
        Timeframe.M1,
        member.range_start,
        member.range_end,
        limit=1,
    )
    return {
        "dataset_revision_id": revision_id,
        "source_dataset_id": lineage.source_dataset_id,
        "instrument_id": member.instrument_id,
        "timeframe": Timeframe.M1.value,
        "range_start": stamp(member.range_start),
        "range_end": stamp(member.range_end),
        "bar_count": lineage.bar_count,
        "calendar_version": revision.calendar_version,
        "canonical_checksum": lineage.canonical_checksum,
        "source_manifest": manifest,
        "source_manifest_hash": lineage.source_checksum,
    }


def publish_history_input(
    connection: Connection,
    store: DatasetStore,
    requests: Mapping[str, str],
) -> dict[str, Any]:
    plan, created_at, acquired_at = _composition_plan(connection, store, requests)
    manifest_json = json.dumps(plan, sort_keys=True, separators=(",", ":"))
    manifest_hash = sha256(manifest_json.encode()).hexdigest()
    revision_id = str(
        uuid5(NAMESPACE_URL, f"stream-analysis/{COMPOSITION_VERSION}/{manifest_hash}")
    )
    if load_dataset_revision(connection, revision_id) is not None:
        return read_history_input(connection, store, revision_id)
    feed = HistoryFeed.model_validate(plan["feed"])
    calendar = feed.calendar()
    count = 0
    first: datetime | None = None
    last: datetime | None = None

    def counted() -> Iterator[Bar]:
        nonlocal count, first, last
        for bar in _input_bars(connection, store, plan, calendar):
            count += 1
            if first is None:
                first = bar.timestamp
            last = bar.timestamp
            yield bar

    canonical_checksum = canonical_bar_checksum_ordered(counted())
    if not count or first is None or last is None:
        raise ValueError("composition has no replay input candles")
    start, end = datetime.fromisoformat(plan["start"]), datetime.fromisoformat(plan["end"])
    source_id = f"{COMPOSITION_VERSION}:{manifest_hash}"
    revision = DatasetRevision(
        revision_id,
        f"shared-history-input-{feed.instrument_id.lower()}",
        source_id,
        "history-composite",
        acquired_at,
        created_at,
        feed.normalization_version,
        feed.calendar_version,
        DATASET_FORMAT_VERSION,
        (DatasetMembership(feed.instrument_id, Timeframe.M1, start, end, count),),
        f"datasets/{revision_id}/manifest.json",
    )
    lineage = DatasetLineage(
        revision_id,
        source_id,
        feed.instrument_id,
        Timeframe.M1,
        start,
        end,
        first,
        last + MINUTE,
        count,
        acquired_at,
        ValidationStatus.PASS,
        manifest_json,
        manifest_hash,
        canonical_checksum,
        BAR_CHECKSUM_VERSION,
        DATASET_FORMAT_VERSION,
    )
    store.publish(connection, revision, lineage, _input_bars(connection, store, plan, calendar))
    return read_history_input(connection, store, revision_id)
