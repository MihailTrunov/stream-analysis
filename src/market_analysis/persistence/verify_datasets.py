"""Verify all PostgreSQL-referenced local dataset revisions after restore."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, select

from .dataset_store import DatasetStore
from .market_data import dataset_revisions


def main() -> None:
    database_url = os.environ["STREAM_ANALYSIS_DATABASE_URL"]
    root = Path(os.environ["STREAM_ANALYSIS_DATA_ROOT"])
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            revisions = connection.scalars(
                select(dataset_revisions.c.dataset_revision_id)
                .order_by(dataset_revisions.c.dataset_revision_id)
            )
            count = 0
            for revision_id in revisions:
                DatasetStore(root).verify_revision(connection, revision_id)
                count += 1
            print(f"Verified {count} dataset revision(s).")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
