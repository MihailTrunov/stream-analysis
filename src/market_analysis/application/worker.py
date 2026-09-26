from __future__ import annotations

import argparse
import signal
import time


def run_worker(kind: str) -> None:
    if kind not in {"evaluation", "import"}:
        raise ValueError(f"unsupported worker kind: {kind}")
    stopped = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopped:
        time.sleep(1.0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("evaluation", "import"))
    args = parser.parse_args()
    run_worker(args.kind)


if __name__ == "__main__":
    main()
