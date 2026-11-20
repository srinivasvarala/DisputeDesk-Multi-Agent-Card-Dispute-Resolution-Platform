"""Integration fixtures: a real Postgres (docker compose locally, a service container in CI).

The tests run in a throwaway database created on that server for the test session and dropped at
the end. This matters because `audit_log` is append-only: rows the tests write could never be
cleaned up from the dev database. The role in DATABASE_URL therefore needs CREATEDB (the
`postgres` role in docker compose and in CI has it). Within the session the tests reseed the
app tables (`--reset` semantics; audit_log is kept). Locally they are skipped if Postgres is not
reachable; in CI (CI env var set) they fail instead.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from disputedesk.config import get_settings
from disputedesk.db import connect
from disputedesk.db.seed import generate, load

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """URL of a throwaway database, created for this session and dropped afterwards."""
    server_url = get_settings().database_url
    try:
        admin = psycopg.connect(server_url, connect_timeout=3, autocommit=True)
    except psycopg.OperationalError as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(f"Postgres not reachable ({exc.__class__.__name__}); run docker compose up -d")
    name = f"disputedesk_test_{uuid.uuid4().hex[:12]}"
    with admin:
        admin.execute(f'CREATE DATABASE "{name}"')  # type: ignore[arg-type]
        try:
            yield urlunsplit(urlsplit(server_url)._replace(path=f"/{name}"))
        finally:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')  # type: ignore[arg-type]


@pytest.fixture
def conn(database_url: str) -> Iterator[psycopg.Connection]:
    """A connection to a freshly reseeded database."""
    with connect(database_url) as connection:
        load(connection, generate(), reset=True)
        yield connection
