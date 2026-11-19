"""Integration fixtures: a real Postgres (docker compose locally, a service container in CI).

These tests reseed the app tables (`--reset` semantics; audit_log is kept). Locally they are
skipped if Postgres is not reachable; in CI (CI env var set) they fail instead.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
import pytest

from disputedesk.config import get_settings
from disputedesk.db import connect
from disputedesk.db.seed import generate, load

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def database_url() -> str:
    url = get_settings().database_url
    try:
        psycopg.connect(url, connect_timeout=3).close()
    except psycopg.OperationalError as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(f"Postgres not reachable ({exc.__class__.__name__}); run docker compose up -d")
    return url


@pytest.fixture
def conn(database_url: str) -> Iterator[psycopg.Connection]:
    """A connection to a freshly reseeded database."""
    with connect(database_url) as connection:
        load(connection, generate(), reset=True)
        yield connection
