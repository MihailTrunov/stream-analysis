"""Shared feed-aware coverage and durable scheduling over immutable M1 imports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, select

from market_analysis.config.oanda_uk_calendars import (
    OANDA_UK_LIVE_PROFILES,
    build_oanda_uk_calendar,
)
from market_analysis.domain import Bar, Timeframe, TradingCalendar
from market_analysis.persistence.dataset_store import DatasetStore
from market_analysis.persistence.history_ranges import (
    HistoryRange,
    create_history_range,
    history_range_requests,
    load_history_range,
    settle_history_range,
)
from market_analysis.persistence.import_jobs import (
    ImportJob,
    ImportJobError,
    ImportRequest,
    ImportStatus,
    create_import_job,
    import_jobs,
    load_import_job,
)
from market_analysis.persistence.market_data import (
    dataset_content_lineage,
    dataset_revisions,
    load_dataset_lineage,
    load_dataset_revision,
)

from .historical_import import NORMALIZATION_VERSION

MINUTE = timedelta(minutes=1)


def stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class HistoryFeed(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    instrument_id: Literal["US30", "DAX"]
    account_fingerprint: str
    provider_symbol: str
    calendar_version: str
    provider: Literal["oanda"] = "oanda"
    environment: Literal["live"] = "live"
    source_id: Literal["oanda-midpoint"] = "oanda-midpoint"
    price_component: Literal["M"] = "M"
    smooth: Literal[False] = False
    normalization_version: str = NORMALIZATION_VERSION

    @property
    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"))

    def calendar(self) -> TradingCalendar:
        profile = OANDA_UK_LIVE_PROFILES.get(self.provider_symbol)
        if (
            profile is None
            or profile.instrument_id != self.instrument_id
            or self.calendar_version != profile.version
            or self.normalization_version != NORMALIZATION_VERSION
            or len(self.account_fingerprint) != 64
            or any(c not in "0123456789abcdef" for c in self.account_fingerprint)
        ):
            raise ValueError("history feed has no exact supported provenance/calendar mapping")
        return build_oanda_uk_calendar(
            provider=self.provider,
            region="UK",
            environment=self.environment,
            account="retained-history",
            provider_symbol=self.provider_symbol,
        )


def request_history(
    connection: Connection,
    feed: HistoryFeed,
    start: datetime,
    end: datetime,
    *,
    at: datetime,
) -> HistoryRange:
    if (
        start.tzinfo is None
        or end.tzinfo is None
        or at.tzinfo is None
        or start >= end
        or end - start > timedelta(days=31)
        or start.second
        or start.microsecond
        or end.second
        or end.microsecond
    ):
        raise ValueError("history range requires aligned aware minutes spanning at most 31 days")
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    if end > at.astimezone(UTC).replace(second=0, microsecond=0):
        raise ValueError("history range includes an incomplete or future source minute")
    # Check the whole range, including closed slots, before retaining work.
    calendar = feed.calendar()
    slot = start
    while slot < end:
        calendar.derive(slot)
        slot += MINUTE
    return create_history_range(connection, feed.canonical_json, start, end, at=at)


@dataclass(frozen=True)
class SourceRange:
    revision_id: str
    checksum: str
    start: datetime
    end: datetime
    gaps: tuple[tuple[datetime, datetime], ...]
    status: str = "available"
    detail: str | None = None
    empty: bool = False


def _sources(
    connection: Connection,
    store: DatasetStore,
    request: HistoryRange,
    feed: HistoryFeed,
) -> list[SourceRange]:
    ids = connection.scalars(
        select(dataset_revisions.c.dataset_revision_id)
        .join(
            dataset_content_lineage,
            dataset_content_lineage.c.dataset_revision_id
            == dataset_revisions.c.dataset_revision_id,
        )
        .where(
            dataset_revisions.c.provider == feed.provider,
            dataset_revisions.c.calendar_version == feed.calendar_version,
            dataset_revisions.c.normalization_version == feed.normalization_version,
            dataset_content_lineage.c.instrument_id == feed.instrument_id,
            dataset_content_lineage.c.timeframe == Timeframe.M1.value,
            dataset_content_lineage.c.requested_start < request.end,
            dataset_content_lineage.c.requested_end > request.start,
        )
        .order_by(dataset_revisions.c.dataset_revision_id)
    )
    result = []
    for revision_id in ids:
        lineage = load_dataset_lineage(connection, revision_id, feed.instrument_id, Timeframe.M1)
        assert lineage is not None
        provenance = json.loads(lineage.provider_request_json)
        required = {
            "provider": feed.provider,
            "environment": feed.environment,
            "symbol": feed.provider_symbol,
            "account_fingerprint": feed.account_fingerprint,
            "source_id": feed.source_id,
            "price_component": feed.price_component,
            "smooth": feed.smooth,
        }
        if any(
            key not in provenance or provenance[key] != value for key, value in required.items()
        ):
            continue
        start, end = (
            max(request.start, lineage.requested_start),
            min(request.end, lineage.requested_end),
        )
        try:
            report = provenance["gap_report"]
            if (
                report["version"] != "calendar-gaps-v1"
                or report["calendar_version"] != feed.calendar_version
            ):
                raise ValueError("retained source has incompatible gap provenance")
            gaps = tuple(
                (datetime.fromisoformat(gap["start"]), datetime.fromisoformat(gap["end"]))
                for gap in report["missing_intervals"]
            )
            if any(
                a.tzinfo is None
                or b.tzinfo is None
                or not lineage.requested_start <= a < b <= lineage.requested_end
                for a, b in gaps
            ):
                raise ValueError("retained source has invalid gap bounds")
            store.read_range(
                connection, revision_id, feed.instrument_id, Timeframe.M1, start, end, limit=1
            )
            status = "unexpected_gap" if report["unexpected_intervals"] else "available"
            result.append(
                SourceRange(
                    revision_id,
                    lineage.canonical_checksum,
                    start,
                    end,
                    gaps,
                    status,
                    empty=lineage.bar_count == 0,
                )
            )
        except (ValueError, KeyError, TypeError, OSError) as error:
            result.append(
                SourceRange(
                    revision_id, lineage.canonical_checksum, start, end, (), "failed", str(error)
                )
            )
    return result


def _jobs(connection: Connection, request: HistoryRange, feed: HistoryFeed) -> list[ImportJob]:
    ids = connection.scalars(
        select(import_jobs.c.job_id)
        .where(
            import_jobs.c.provider == feed.provider,
            import_jobs.c.environment == feed.environment,
            import_jobs.c.instrument_id == feed.instrument_id,
            import_jobs.c.provider_symbol == feed.provider_symbol,
            import_jobs.c.account_fingerprint == feed.account_fingerprint,
            import_jobs.c.calendar_version == feed.calendar_version,
            import_jobs.c.timeframe == Timeframe.M1.value,
            import_jobs.c.requested_start < request.end,
            import_jobs.c.requested_end > request.start,
        )
        .order_by(import_jobs.c.created_at.desc(), import_jobs.c.job_id.desc())
    )
    result = []
    for job_id in ids:
        job = load_import_job(connection, job_id)
        if job is None:
            continue
        if job.status is ImportStatus.COMPLETED:
            revision = load_dataset_revision(connection, job.revision_id)
            if revision and revision.normalization_version != feed.normalization_version:
                continue
        result.append(job)
    return result


def history_coverage(
    connection: Connection,
    store: DatasetStore,
    request_id: str,
) -> dict[str, Any]:
    request = load_history_range(connection, request_id)
    feed = HistoryFeed.model_validate_json(request.feed_json)
    calendar = feed.calendar()
    sources, jobs = _sources(connection, store, request, feed), _jobs(connection, request, feed)
    boundaries = {request.start, request.end}

    def include(start: datetime, end: datetime) -> None:
        if start < request.end and end > request.start:
            boundaries.update((max(start, request.start), min(end, request.end)))

    for source in sources:
        include(source.start, source.end)
        for start, end in source.gaps:
            include(start, end)
    for job in jobs:
        include(job.request.start, job.request.end)
    slot, prior_open = request.start, None
    while slot < request.end:
        is_open = calendar.derive(slot).is_open
        if is_open != prior_open:
            boundaries.add(slot)
        slot, prior_open = slot + MINUTE, is_open
    ordered = sorted(boundaries)
    spans: list[dict[str, Any]] = []
    for start, end in zip(ordered, ordered[1:], strict=False):
        item: dict[str, Any] = {"start": stamp(start), "end": stamp(end)}
        covering = [s for s in sources if s.start <= start < s.end]
        available = [
            s
            for s in covering
            if s.status == "available" and not any(a <= start < b for a, b in s.gaps)
        ]
        if not calendar.derive(start).is_open:
            unexpected = next((s for s in covering if s.status == "unexpected_gap"), None)
            if unexpected:
                item.update(status="unexpected_gap", revision_id=unexpected.revision_id)
            else:
                item["status"] = "closed"
        elif available:
            identities = {(s.checksum, s.start, s.end) for s in available}
            if len(identities) > 1:
                item.update(status="conflict", revision_ids=[s.revision_id for s in available])
            else:
                item.update(
                    status="available",
                    revision_id=available[0].revision_id,
                    canonical_checksum=available[0].checksum,
                )
        elif pending := next(
            (
                j
                for j in jobs
                if j.request.start <= start < j.request.end
                and j.status in {ImportStatus.QUEUED, ImportStatus.RUNNING}
            ),
            None,
        ):
            item.update(status="pending", job_id=pending.job_id, import_status=pending.status.value)
        elif covering:
            source = covering[0]
            item.update(
                status=source.status if source.status != "available" else "unexpected_gap",
                revision_id=source.revision_id,
                detail=source.detail,
            )
            # Empty open windows are provider-unavailable, not computed zero-pattern windows.
            if source.status == "available" and source.empty:
                item["status"] = "unavailable"
        elif terminal := next((j for j in jobs if j.request.start <= start < j.request.end), None):
            item.update(
                status="failed",
                job_id=terminal.job_id,
                detail=terminal.failure_reason or "published import result is unavailable",
            )
        else:
            item["status"] = "missing"
        if request.failure_reason and item["status"] in {"missing", "failed"}:
            item.update(status="failed", detail=request.failure_reason)
        if spans and {k: v for k, v in spans[-1].items() if k not in {"start", "end"}} == {
            k: v for k, v in item.items() if k not in {"start", "end"}
        }:
            spans[-1]["end"] = item["end"]
        else:
            spans.append(item)
    states = {span["status"] for span in spans}
    status = next(
        (s for s in ("conflict", "failed", "unexpected_gap", "unavailable") if s in states),
        "pending" if states & {"missing", "pending"} else "available",
    )
    manifest = [
        {key: span[key] for key in ("start", "end", "revision_id", "canonical_checksum")}
        for span in spans
        if span["status"] == "available"
    ]
    manifest_json = json.dumps(
        {"feed": feed.model_dump(), "sources": manifest}, sort_keys=True, separators=(",", ":")
    )
    return {
        "request_id": request_id,
        "instrument_id": feed.instrument_id,
        "start": stamp(request.start),
        "end": stamp(request.end),
        "status": status,
        "spans": spans,
        "source_manifest": json.loads(manifest_json),
        "source_manifest_hash": sha256(manifest_json.encode()).hexdigest(),
    }


def schedule_history_import(
    connection: Connection,
    store: DatasetStore,
    *,
    account: str,
    environment: str,
    at: datetime,
) -> ImportJob | None:
    """Fill one free import slot; durable windows retain the remaining intent."""
    if connection.scalar(
        select(import_jobs.c.job_id).where(import_jobs.c.active_slot == 1).limit(1)
    ):
        return None
    query = (
        select(history_range_requests.c.request_id)
        .where(
            history_range_requests.c.settled.is_(False),
        )
        .order_by(history_range_requests.c.created_at, history_range_requests.c.request_id)
        .limit(20)
    )
    if connection.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    for request_id in connection.scalars(query):
        request = load_history_range(connection, request_id)
        try:
            feed = HistoryFeed.model_validate_json(request.feed_json)
        except ValueError as error:
            settle_history_range(connection, request_id, str(error))
            continue
        if (
            feed.account_fingerprint != sha256(account.encode()).hexdigest()
            or feed.environment != environment
        ):
            settle_history_range(
                connection, request_id, "history account/environment context has changed"
            )
            continue
        try:
            coverage = history_coverage(connection, store, request_id)
        except ValueError as error:
            # A retired calendar or invalid retained request must not stop the
            # worker from servicing other ranges or explicit import jobs.
            settle_history_range(connection, request_id, str(error))
            continue
        missing = next((span for span in coverage["spans"] if span["status"] == "missing"), None)
        if missing is None:
            if not any(span["status"] == "pending" for span in coverage["spans"]):
                settle_history_range(connection, request_id)
            continue
        selected = ImportRequest(
            f"shared-history-{sha256(feed.canonical_json.encode()).hexdigest()[:24]}",
            feed.instrument_id,
            Timeframe.M1,
            datetime.fromisoformat(missing["start"]),
            datetime.fromisoformat(missing["end"]),
            feed.provider,
            feed.environment,
            feed.provider_symbol,
            feed.calendar_version,
            feed.account_fingerprint,
        )
        try:
            return create_import_job(
                connection, selected, job_id=str(uuid4()), revision_id=str(uuid4()), at=at
            )
        except ImportJobError as error:
            if "already active" not in str(error):
                raise
            return None  # A concurrent scheduler/manual import won the unique slot.
    return None


def history_prices(
    connection: Connection,
    store: DatasetStore,
    request_id: str,
    *,
    limit: int = 500,
) -> dict[str, Any]:
    if isinstance(limit, bool) or not 1 <= limit <= 500:
        raise ValueError("history price limit must be between 1 and 500")
    coverage = history_coverage(connection, store, request_id)
    if coverage["status"] == "conflict":
        raise ValueError("conflicting retained revisions require explicit source resolution")
    bars: list[Bar] = []
    for span in reversed(coverage["spans"]):
        if span["status"] != "available":
            continue
        selected = store.read_range(
            connection,
            span["revision_id"],
            coverage["instrument_id"],
            Timeframe.M1,
            datetime.fromisoformat(span["start"]),
            datetime.fromisoformat(span["end"]),
            limit=limit - len(bars),
        )
        bars[:0] = selected
        if len(bars) == limit:
            break
    return {
        **coverage,
        "bars": [
            {**dict(bar.to_canonical_dict()), "available_at": stamp(bar.timestamp + MINUTE)}
            for bar in bars
        ],
        "limit": limit,
    }
