"""Shared disposable database fixtures; never targets the installed application URL."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema


@pytest.fixture
def isolated_postgres_url():
    """Isolate fixtures with conflicting synthetic instrument/calendar identities."""
    url = os.getenv("STREAM_ANALYSIS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("PostgreSQL integration URL is not configured")
    schema = f"fixture_{uuid4().hex}"
    admin = create_engine(url)
    try:
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))
        try:
            yield make_url(url).update_query_dict(
                {"options": f"-csearch_path={schema}"},
            ).render_as_string(hide_password=False)
        finally:
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
    finally:
        admin.dispose()
