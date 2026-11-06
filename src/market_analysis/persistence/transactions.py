"""Real transaction boundaries, including SQLite's legacy SAVEPOINT behavior."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine


@contextmanager
def atomic_transaction(engine: Engine) -> Iterator[Connection]:
    with engine.begin() as connection:
        if connection.dialect.name == "sqlite" and not getattr(
            connection.connection.driver_connection,
            "in_transaction",
            False,
        ):
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        yield connection
