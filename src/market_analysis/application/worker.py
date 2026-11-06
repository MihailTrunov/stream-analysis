from __future__ import annotations

import argparse
import os
import signal
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from sqlalchemy import Engine, create_engine

from market_analysis.application.autonomous_evaluation import (
    execute_evaluation,
    validate_chunk_size,
)
from market_analysis.application.diagnostics import write_worker_heartbeat
from market_analysis.application.evaluation_scheduler import run_scheduler_once, set_automatic
from market_analysis.application.historical_import import advance_import
from market_analysis.application.history_ranges import schedule_history_import
from market_analysis.application.live_retention import package_pending_live_hours
from market_analysis.application.logging import configure_logging, research_logger
from market_analysis.config.oanda_uk_calendars import build_oanda_uk_calendar
from market_analysis.domain.historical_data import ProviderError
from market_analysis.domain.session_calendar import SessionCalendarError
from market_analysis.persistence.dataset_store import DatasetStore, DatasetStoreError
from market_analysis.persistence.import_batches import ImportBatchStore
from market_analysis.persistence.import_jobs import (
    ImportJob,
    ImportJobError,
    ImportStatus,
    claim_import_job,
    fail_import_job,
    interrupt_running_imports,
    touch_import_heartbeat,
)
from market_analysis.persistence.transactions import atomic_transaction
from market_analysis.providers.oanda import OandaHistoricalDataSource


def _clock() -> datetime:
    return datetime.now(UTC)


def _import_credentials() -> tuple[str, str, str] | None:
    token = os.getenv("OANDA_KEY") or os.getenv("OANDA_TOKEN") or ""
    account = os.getenv("OANDA_ACCOUNT") or ""
    environment = (os.getenv("OANDA_ENV") or "live").lower()
    region = (os.getenv("OANDA_REGION") or "UK").upper()
    if not token or not account or environment != "live" or region != "UK":
        return None
    return token, account, environment


def _heartbeat_while_importing(
    engine: Engine,
    data_root: Path,
    job_id: str,
    stopped: threading.Event,
) -> None:
    logger = research_logger(component="import-worker", run_id=job_id)
    while not stopped.wait(5):
        try:
            write_worker_heartbeat(data_root, "import")
            with engine.begin() as connection:
                if not touch_import_heartbeat(connection, job_id, at=_clock()):
                    return
        except Exception:
            logger.exception("import heartbeat failed")


def _run_import_job(
    engine: Engine,
    data_root: Path,
    job: ImportJob,
    credentials: tuple[str, str, str],
    should_stop: Callable[[], bool],
) -> None:
    token, account, environment = credentials
    logger = research_logger(component="import-worker", run_id=job.job_id)
    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_while_importing,
        args=(engine, data_root, job.job_id, heartbeat_stop),
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        if (
            job.request.account_fingerprint is not None
            and sha256(account.encode()).hexdigest() != job.request.account_fingerprint
        ):
            raise ImportJobError("import account differs from its original account context")
        calendar = build_oanda_uk_calendar(
            provider="oanda",
            region="UK",
            environment=environment,
            account=account,
            provider_symbol=job.request.provider_symbol,
            calendar_version=job.request.calendar_version,
        )
        with OandaHistoricalDataSource(account, token, environment) as source:
            source.verify_account_instrument(job.request.provider_symbol)
            dataset_store = DatasetStore(data_root)
            batch_store = ImportBatchStore(data_root)
            while not should_stop():
                job = advance_import(
                    engine,
                    source,
                    dataset_store,
                    batch_store,
                    job.job_id,
                    calendar,
                )
                try:
                    package_pending_live_hours(engine, dataset_store, at=_clock())
                except Exception:
                    logger.exception("live packaging failed between import pages")
                if job.status is ImportStatus.COMPLETED:
                    logger.info("import completed", extra={"bar_count": job.bar_count})
                    return
        with engine.begin() as connection:
            interrupt_running_imports(connection, at=_clock())
        logger.info("import interrupted by worker stop")
    except (ProviderError, ImportJobError, DatasetStoreError, SessionCalendarError) as exc:
        with engine.begin() as connection:
            fail_import_job(connection, job.job_id, at=_clock(), reason=str(exc))
        logger.warning("import failed", extra={"reason": str(exc)})
    except Exception:
        with engine.begin() as connection:
            fail_import_job(connection, job.job_id, at=_clock(), reason="unexpected import failure")
        logger.exception("unexpected import failure")
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=6)


def _run_import_worker(data_root: Path, should_stop: Callable[[], bool]) -> None:
    database_url = os.getenv("STREAM_ANALYSIS_DATABASE_URL")
    if not database_url:
        raise RuntimeError("import worker needs STREAM_ANALYSIS_DATABASE_URL")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            interrupt_running_imports(connection, at=_clock())
        while not should_stop():
            write_worker_heartbeat(data_root, "import")
            try:
                package_pending_live_hours(engine, DatasetStore(data_root), at=_clock())
            except Exception:
                research_logger(component="import-worker").exception("live packaging failed")
            credentials = _import_credentials()
            if credentials is None:
                time.sleep(1.0)
                continue
            with engine.begin() as connection:
                schedule_history_import(
                    connection, DatasetStore(data_root), account=credentials[1],
                    environment=credentials[2], at=_clock(),
                )
                job = claim_import_job(connection, at=_clock())
            if job is None:
                time.sleep(1.0)
                continue
            _run_import_job(engine, data_root, job, credentials, should_stop)
    finally:
        engine.dispose()


def run_worker(
    kind: str, *, run_id: str | None = None, chunk_size: int = 128, once: bool = False,
) -> None:
    if kind not in {"evaluation", "import"}:
        raise ValueError(f"unsupported worker kind: {kind}")
    if kind == "evaluation":
        validate_chunk_size(chunk_size)
    data_root = Path(os.getenv("STREAM_ANALYSIS_DATA_ROOT", "./data"))
    configure_logging(data_root=data_root)
    logger = research_logger(component=f"{kind}-worker")
    logger.info("worker started")
    stopped = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if run_id is not None:
        if kind != "evaluation":
            raise ValueError("explicit evaluation run IDs are not import-worker inputs")
        url = os.getenv("STREAM_ANALYSIS_DATABASE_URL")
        if not url:
            raise ValueError("database is not configured")
        if hasattr(os, "nice"):
            try:
                os.nice(5)
            except OSError:
                logger.warning("evaluation process priority could not be lowered")
        engine = create_engine(url)
        try:
            write_worker_heartbeat(data_root, kind)
            result = execute_evaluation(
                engine, DatasetStore(data_root), run_id, chunk_size=chunk_size,
                should_stop=lambda: stopped,
                on_commit=lambda: write_worker_heartbeat(data_root, kind),
                on_heartbeat=lambda: write_worker_heartbeat(data_root, kind),
            )
            logger.info("explicit evaluation worker finished", extra={
                "run_id": run_id, "status": result.status.value,
            })
        finally:
            engine.dispose()
        return
    if kind == "import":
        _run_import_worker(data_root, lambda: stopped)
        logger.info("worker stopped")
        return
    url = os.getenv("STREAM_ANALYSIS_DATABASE_URL")
    if not url:
        raise ValueError("database is not configured")
    if hasattr(os, "nice"):
        try:
            os.nice(5)
        except OSError:
            logger.warning("evaluation process priority could not be lowered")
    engine = create_engine(url)
    try:
        while not stopped:
            write_worker_heartbeat(data_root, kind)
            job_result = run_scheduler_once(
                engine, DatasetStore(data_root), chunk_size=chunk_size,
                should_stop=lambda: stopped,
                on_heartbeat=lambda: write_worker_heartbeat(data_root, kind),
            )
            if once:
                break
            if job_result is None:
                time.sleep(1.0)
        if stopped:
            with atomic_transaction(engine) as connection:
                set_automatic(connection, False)
    finally:
        engine.dispose()
    logger.info("worker stopped")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("evaluation", "import"))
    parser.add_argument(
        "--run-id", help="execute one frozen evaluation directly instead of consuming the queue",
    )
    parser.add_argument("--chunk-size", type=int, default=128)
    parser.add_argument("--once", action="store_true", help="poll the evaluation queue once")
    args = parser.parse_args()
    run_worker(args.kind, run_id=args.run_id, chunk_size=args.chunk_size, once=args.once)


if __name__ == "__main__":
    main()
