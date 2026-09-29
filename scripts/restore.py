"""Restore a portable archive into an empty local data root and database."""

from __future__ import annotations

import argparse

from _data_operations import restore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", help="backup archive path")
    args = parser.parse_args()
    restore(args.archive)
    print("Restore complete; dataset lineage and checksums verified.")


if __name__ == "__main__":
    main()
