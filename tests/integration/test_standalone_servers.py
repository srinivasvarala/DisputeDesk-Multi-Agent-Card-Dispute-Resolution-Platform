"""Each server starts standalone (python -m ...) over stdio, against Postgres, and audits calls."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters, get_default_environment
from mcp_types import Implementation

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

REPO_ROOT = Path(__file__).resolve().parents[2]

SERVERS = {
    "transactions": (
        {"get_txn", "list_txns", "get_history"},
        ("get_txn", {"txn_id": "txn_000001"}),
    ),
    "merchant": (
        {"get_profile", "get_dispute_rate", "get_delivery_status"},
        ("get_profile", {"merchant_id": "mer_001"}),
    ),
    "cases": (
        {"get_case", "save_draft", "update_status"},
        ("get_case", {"case_id": "case_0001"}),
    ),
}


@pytest.mark.parametrize("name", sorted(SERVERS))
async def test_server_starts_over_stdio_and_audits(
    name: str, database_url: str, conn: psycopg.Connection
) -> None:
    expected_tools, (tool, args) = SERVERS[name]
    actor = f"it-{uuid.uuid4().hex[:8]}"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", f"disputedesk.mcp_servers.{name}"],
        env={**get_default_environment(), "DATABASE_URL": database_url},
        cwd=str(REPO_ROOT),
    )
    async with Client(params, client_info=Implementation(name=actor, version="0")) as client:
        assert {t.name for t in (await client.list_tools()).tools} == expected_tools
        good = await client.call_tool(tool, args)
        bad = await client.call_tool(tool, {})
    assert not good.is_error
    assert bad.is_error

    rows = conn.execute(
        "SELECT server, tool, outcome FROM audit_log WHERE actor = %s ORDER BY id", (actor,)
    ).fetchall()
    assert rows == [(name, tool, "ok"), (name, tool, "invalid_input")]


def test_client_demo_script_calls_every_tool(database_url: str, conn: psycopg.Connection) -> None:
    result = subprocess.run(
        [sys.executable, "scripts/mcp_client_demo.py"],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    for tools, _ in SERVERS.values():
        for tool in tools:
            assert f"-> {tool}(" in result.stdout, f"{tool} was not called"
