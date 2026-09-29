"""Create a manual portable backup of a stopped local installation."""

from __future__ import annotations

import argparse

from _data_operations import backup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "archive", nargs="?", help="new archive path; defaults to backups/<UTC>.zip"
    )
    args = parser.parse_args()
    print(backup(args.archive))


if __name__ == "__main__":
    main()
