from __future__ import annotations

import os

from sqlalchemy import create_engine

from alembic import context
from market_analysis.persistence import (
    import_jobs,  # noqa: F401
    market_data,  # noqa: F401
    pattern_instances,  # noqa: F401
    replay_runs,  # noqa: F401
    validation_annotations,  # noqa: F401
)
from market_analysis.persistence.runs import metadata

target_metadata = metadata


def run_migrations_online() -> None:
    database_url = os.environ.get("STREAM_ANALYSIS_DATABASE_URL")
    if not database_url:
        raise RuntimeError("STREAM_ANALYSIS_DATABASE_URL is required for migrations")
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run_migrations_online()
