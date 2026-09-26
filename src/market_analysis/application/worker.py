from __future__ import annotations

import argparse
import os
import signal
import time

from market_analysis.application.diagnostics import write_worker_heartbeat
from market_analysis.application.logging import configure_logging, research_logger


def run_worker(kind: str) -> None:
    if kind not in {"evaluation", "import"}:
        raise ValueError(f"unsupported worker kind: {kind}")
    data_root = os.getenv("STREAM_ANALYSIS_DATA_ROOT", "./data")
    configure_logging(data_root=data_root)
    logger = research_logger(component=f"{kind}-worker")
    logger.info("worker started")
    stopped = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
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
