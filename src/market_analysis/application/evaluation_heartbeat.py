"""Heartbeat ownership while an explicit executor loads data or processes a chunk."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Event, Thread

from sqlalchemy import Engine

from market_analysis.application.logging import research_logger
from market_analysis.persistence.evaluations import touch_evaluation
from market_analysis.persistence.transactions import atomic_transaction


@contextmanager
def maintain_evaluation_heartbeat(
    engine: Engine,
    evaluation_id: str,
    token: str,
    *,
    interval: float,
    on_heartbeat: Callable[[], None] | None = None,
) -> Iterator[None]:
    stopped = Event()
    logger = research_logger(component="evaluation-heartbeat", run_id=evaluation_id)

    def periodic() -> None:
        while not stopped.wait(interval):
            try:
                with atomic_transaction(engine) as connection:
                    if not touch_evaluation(connection, evaluation_id, token, datetime.now(UTC)):
                        return
                if on_heartbeat is not None:
                    on_heartbeat()
            except Exception:
                logger.exception("explicit evaluation heartbeat failed")

    thread = Thread(target=periodic, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=6)
