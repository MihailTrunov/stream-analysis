"""Provider-neutral checkpointed acquisition into immutable dataset revisions."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

from sqlalchemy import Engine

from market_analysis.domain.dataset_lineage import (
    BAR_CHECKSUM_VERSION,
    DatasetLineage,
    canonical_bar_checksum_ordered,
)
from market_analysis.domain.dataset_validation import ValidationStatus
from market_analysis.domain.historical_data import (
    HistoricalDataRequest,
    HistoricalDataSource,
    HistoricalSource,
)
from market_analysis.domain.market_data import Bar, Instrument
from market_analysis.domain.session_calendar import SessionCalendarError, TradingCalendar
from market_analysis.persistence.dataset_store import DATASET_FORMAT_VERSION, DatasetStore
from market_analysis.persistence.import_batches import ImportBatchStore
from market_analysis.persistence.import_jobs import (
    ImportBatch,
    ImportJob,
    ImportJobError,
    ImportStatus,
    checkpoint_import_page,
    complete_import_job,
    list_import_batches,
    load_import_job,
)
from market_analysis.persistence.market_data import (
    DatasetMembership,
    DatasetRevision,
    load_instrument,
)

NORMALIZATION_VERSION = "canonical-midpoint-bars-v1"
_MINUTE = timedelta(minutes=1)


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _check_calendar(job: ImportJob, instrument: Instrument, calendar: TradingCalendar) -> None:
    if (
        calendar.provider != job.request.provider
        or calendar.instrument_id != job.request.instrument_id
        or calendar.calendar_id != instrument.calendar_id
        or calendar.version != job.request.calendar_version
    ):
        raise ImportJobError("import has no exact versioned calendar mapping")
    if (
        job.request.account_fingerprint is not None
        and sha256(calendar.account.encode()).hexdigest() != job.request.account_fingerprint
    ):
        raise ImportJobError("import account differs from its original account context")


def _append_interval(items: list[dict[str, str]], timestamp: datetime) -> None:
    start, end = _stamp(timestamp), _stamp(timestamp + _MINUTE)
    if items and items[-1]["end"] == start:
        items[-1]["end"] = end
    else:
        items.append({"start": start, "end": end})


def _gap_report(
    job: ImportJob,
    calendar: TradingCalendar,
    bars: Iterable[Bar],
) -> tuple[str, ValidationStatus]:
    """Merge ordered bars with expected open M1 slots in bounded memory."""
    missing: list[dict[str, str]] = []
    unexpected: list[dict[str, str]] = []
    stream = iter(bars)
    next_bar = next(stream, None)
    start = job.request.start
    slot = start.replace(second=0, microsecond=0)
    if slot < start:
        slot += _MINUTE
    expected_count = 0
    while slot < job.request.end:
        try:
            open_slot = calendar.derive(slot).is_open
        except SessionCalendarError as exc:
            raise ImportJobError("import is outside verified calendar coverage") from exc
        expected_count += open_slot
        if next_bar is not None and next_bar.timestamp < slot:
            raise ImportJobError("import contains an unaligned or out-of-range M1 bar")
        if next_bar is not None and next_bar.timestamp == slot:
            if not open_slot:
                _append_interval(unexpected, slot)
            next_bar = next(stream, None)
        elif open_slot:
            _append_interval(missing, slot)
        slot += _MINUTE
    if next_bar is not None:
        raise ImportJobError("import contains a bar outside requested M1 slots")
    report: dict[str, Any] = {
        "version": "calendar-gaps-v1",
        "calendar_id": calendar.calendar_id,
        "calendar_version": calendar.version,
        "expected_slot_count": expected_count,
        "missing_intervals": missing,
        "unexpected_intervals": unexpected,
    }
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":"))
    return encoded, ValidationStatus.WARNING if missing or unexpected else ValidationStatus.PASS


def _source_checksum(bars: Iterable[Bar]) -> str:
    digest = sha256(b"normalized-source-bars-v1\n")
    for bar in bars:
        payload = json.dumps(
            dict(bar.to_canonical_dict()), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        digest.update(payload.encode() + b"\n")
    return digest.hexdigest()


def _publication(
    job: ImportJob,
    calendar: TradingCalendar,
    batch_store: ImportBatchStore,
    batches: tuple[ImportBatch, ...],
) -> tuple[DatasetRevision, DatasetLineage, str]:
    if job.source_id is None or job.retrieved_at is None:
        raise ImportJobError("completed fetch lacks source or retrieval metadata")

    def bars() -> Iterable[Bar]:
        return batch_store.iter_bars(batches)

    report_json, validation_status = _gap_report(job, calendar, bars())
    canonical_checksum = canonical_bar_checksum_ordered(bars())
    source_checksum = _source_checksum(bars())
    provenance: dict[str, object] = {
        "provider": job.request.provider,
        "environment": job.request.environment,
        "symbol": job.request.provider_symbol,
        "timeframe": job.request.timeframe.value,
        "requested_start": _stamp(job.request.start),
        "requested_end": _stamp(job.request.end),
        "source_id": job.source_id,
        "gap_report": json.loads(report_json),
    }
    if job.request.provider == "oanda" and job.source_id == "oanda-midpoint":
        provenance["price_component"] = "M"
        provenance["smooth"] = False
    if job.request.account_fingerprint is not None:
        provenance["account_fingerprint"] = job.request.account_fingerprint
    provider_request_json = json.dumps(provenance, sort_keys=True, separators=(",", ":"))
    revision = DatasetRevision(
        job.revision_id,
        job.request.dataset_id,
        job.job_id,
        job.request.provider,
        job.retrieved_at,
        job.created_at,
        NORMALIZATION_VERSION,
        job.request.calendar_version,
        DATASET_FORMAT_VERSION,
        (
            DatasetMembership(
                job.request.instrument_id,
                job.request.timeframe,
                job.request.start,
                job.request.end,
                job.bar_count,
            ),
        ),
        f"datasets/{job.revision_id}/manifest.json",
    )
    lineage = DatasetLineage(
        job.revision_id,
        job.job_id,
        job.request.instrument_id,
        job.request.timeframe,
        job.request.start,
        job.request.end,
        job.actual_start,
        job.actual_end,
        job.bar_count,
        job.retrieved_at,
        validation_status,
        provider_request_json,
        source_checksum,
        canonical_checksum,
        BAR_CHECKSUM_VERSION,
        DATASET_FORMAT_VERSION,
    )
    return revision, lineage, report_json


def _finalize(
    engine: Engine,
    dataset_store: DatasetStore,
    batch_store: ImportBatchStore,
    job_id: str,
    calendar: TradingCalendar,
    *,
    at: datetime,
) -> ImportJob:
    with engine.begin() as connection:
        job = load_import_job(connection, job_id)
        if job is None or job.status is not ImportStatus.RUNNING or not job.fetch_complete:
            raise ImportJobError("import is not ready for publication")
        instrument = load_instrument(connection, job.request.instrument_id)
        if instrument is None:
            raise ImportJobError("import instrument is not registered")
        _check_calendar(job, instrument, calendar)
        batches = list_import_batches(connection, job_id)
        if (
            len(batches) != job.pages_committed
            or sum(b.bar_count for b in batches) != job.bar_count
        ):
            raise ImportJobError("durable import batches differ from checkpoint")
        revision, lineage, report_json = _publication(job, calendar, batch_store, batches)
        dataset_store.publish(connection, revision, lineage, batch_store.iter_bars(batches))
        return complete_import_job(connection, job_id, at=at, gap_report_json=report_json)


def advance_import(
    engine: Engine,
    source: HistoricalDataSource,
    dataset_store: DatasetStore,
    batch_store: ImportBatchStore,
    job_id: str,
    calendar: TradingCalendar,
    *,
    at: datetime | None = None,
) -> ImportJob:
    """Fetch and checkpoint one source page, then publish only on terminal fetch."""
    at = _now() if at is None else at
    with engine.connect() as connection:
        job = load_import_job(connection, job_id)
        if job is None or job.status is not ImportStatus.RUNNING:
            raise ImportJobError("import must be claimed before acquisition")
        instrument = load_instrument(connection, job.request.instrument_id)
        previous_batches = list_import_batches(connection, job_id)
    if instrument is None:
        raise ImportJobError("import instrument is not registered")
    _check_calendar(job, instrument, calendar)
    if job.fetch_complete:
        return _finalize(engine, dataset_store, batch_store, job_id, calendar, at=at)
    selected = HistoricalDataRequest(
        instrument,
        job.request.timeframe,
        job.request.start,
        job.request.end,
        job.next_page_token,
    )
    page = source.get_bars(selected)
    if page.request != selected:
        raise ImportJobError("source returned a page for another import request")
    expected_source = HistoricalSource(
        job.request.provider,
        job.request.provider_symbol,
        page.source.source_id,
        job.request.environment,
    )
    if page.source != expected_source:
        raise ImportJobError("source metadata does not match import request")
    if job.source_id is not None and page.source.source_id != job.source_id:
        raise ImportJobError("source identity changed after checkpoint")
    bars = page.bars
    if previous_batches and bars:
        previous = batch_store.read_batch(previous_batches[-1])[-1]
        if bars[0].timestamp < previous.timestamp:
            raise ImportJobError("source page moved backward across checkpoint")
        if bars[0].timestamp == previous.timestamp:
            if bars[0] != previous:
                raise ImportJobError("conflicting duplicate bar across source pages")
            bars = bars[1:]
    batch = batch_store.write_batch(job_id, job.pages_committed, bars) if bars else None
    with engine.begin() as connection:
        changed = checkpoint_import_page(
            connection,
            job_id,
            expected_token=job.next_page_token,
            next_token=page.next_page_token,
            source_id=page.source.source_id,
            batch=batch,
            at=at,
        )
    if changed.fetch_complete:
        return _finalize(engine, dataset_store, batch_store, job_id, calendar, at=at)
    return changed
