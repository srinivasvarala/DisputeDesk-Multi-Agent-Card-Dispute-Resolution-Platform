"""Postgres access: connection helper and idempotent schema setup."""

from __future__ import annotations

from importlib.resources import files

import psycopg

from disputedesk.config import get_settings


def connect(database_url: str | None = None) -> psycopg.Connection:
    """Open an autocommit connection. Use `with conn.transaction():` for multi-statement writes."""
    url = database_url or get_settings().database_url
    return psycopg.connect(url, autocommit=True)


def apply_schema(conn: psycopg.Connection) -> None:
    """Create all app tables if they do not exist. Safe to run repeatedly."""
    sql = files("disputedesk.db").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.execute(sql)
