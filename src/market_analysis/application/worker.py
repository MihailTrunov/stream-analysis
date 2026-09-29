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

from market_analysis.application.diagnostics import write_worker_heartbeat
from market_analysis.application.historical_import import advance_import
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
            credentials = _import_credentials()
            if credentials is None:
                time.sleep(1.0)
                continue
            with engine.begin() as connection:
                job = claim_import_job(connection, at=_clock())
            if job is None:
                time.sleep(1.0)
                continue
            _run_import_job(engine, data_root, job, credentials, should_stop)
    finally:
        engine.dispose()


def run_worker(kind: str) -> None:
    if kind not in {"evaluation", "import"}:
        raise ValueError(f"unsupported worker kind: {kind}")
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
    if kind == "import":
        _run_import_worker(data_root, lambda: stopped)
        logger.info("worker stopped")
        return
    while not stopped:
        write_worker_heartbeat(data_root, kind)
        time.sleep(1.0)
    logger.info("worker stopped")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("evaluation", "import"))
    args = parser.parse_args()
    run_worker(args.kind)


if __name__ == "__main__":
    main()
