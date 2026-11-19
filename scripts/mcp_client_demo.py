"""MCP client demo: start each DisputeDesk server over stdio, list its tools and call every one.

    docker compose up -d && uv run python -m disputedesk.db.seed --reset
    uv run python scripts/mcp_client_demo.py

Each server also gets one deliberately invalid call to show input validation. At the end the
script counts this run's audit_log rows to show that every call (good or bad) was recorded.
Note: the cases calls write data (a new draft and a status change on case_0001).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from typing import Any

from mcp import Client
from mcp.client.stdio import StdioServerParameters, get_default_environment
from mcp_types import CallToolResult, Implementation

from disputedesk.config import get_settings
from disputedesk.db import connect

CLIENT_NAME = "mcp-client-demo"
DEMO_CASE = "case_0001"


def _server(module: str) -> StdioServerParameters:
    env = {**get_default_environment(), "DATABASE_URL": get_settings().database_url}
    return StdioServerParameters(
        command=sys.executable, args=["-m", module], env=env, cwd=os.getcwd()
    )


def _show(tool: str, args: dict[str, Any], result: CallToolResult) -> None:
    status = "ERROR" if result.is_error else "ok"
    print(f"  -> {tool}({json.dumps(args)}) [{status}]")
    if result.is_error:
        text = " ".join(getattr(c, "text", "") for c in result.content)
        print(f"     {text.splitlines()[0][:150]}")
    else:
        body = json.dumps(result.structured_content, default=str)
        print(f"     {body[:300]}{'...' if len(body) > 300 else ''}")


async def _call(client: Client, tool: str, args: dict[str, Any]) -> CallToolResult:
    result = await client.call_tool(tool, args)
    _show(tool, args, result)
    return result


async def _connect(name: str, module: str) -> Client:
    print(f"\n== {name} ({module})")
    return Client(_server(module), client_info=Implementation(name=CLIENT_NAME, version="1"))


async def _list(client: Client) -> None:
    for tool in (await client.list_tools()).tools:
        hints = tool.annotations
        kind = "read-only" if hints and hints.read_only_hint else "WRITE"
        print(f"  tool: {tool.name:<20} [{kind}] {(tool.description or '').splitlines()[0]}")


async def demo_transactions() -> int:
    errors = 0
    async with await _connect("transactions", "disputedesk.mcp_servers.transactions") as client:
        await _list(client)
        txn = (await _call(client, "get_txn", {"txn_id": "txn_000001"})).structured_content or {}
        customer = txn.get("customer_id", "cus_0001")
        await _call(client, "list_txns", {"customer_id": customer, "limit": 3})
        await _call(client, "get_history", {"customer_id": customer, "days": 365})
        bad = await _call(client, "get_txn", {"txn_id": "not-a-txn"})
        errors += 0 if bad.is_error else 1
    return errors


async def demo_merchant() -> int:
    errors = 0
    async with await _connect("merchant", "disputedesk.mcp_servers.merchant") as client:
        await _list(client)
        await _call(client, "get_profile", {"merchant_id": "mer_001"})
        await _call(client, "get_dispute_rate", {"merchant_id": "mer_001", "window_days": 365})
        await _call(client, "get_delivery_status", {"txn_id": "txn_000001"})
        bad = await _call(client, "get_dispute_rate", {"merchant_id": "mer_001", "window_days": 0})
        errors += 0 if bad.is_error else 1
    return errors


async def demo_cases() -> int:
    errors = 0
    async with await _connect("cases", "disputedesk.mcp_servers.cases") as client:
        await _list(client)
        await _call(
            client,
            "save_draft",
            {
                "case_id": DEMO_CASE,
                "decision": "escalate",
                "rationale": "Demo draft from the MCP client script.",
                "letter": "Dear merchant, this dispute is under review.",
                "author": "demo-client",
            },
        )
        view = (await _call(client, "get_case", {"case_id": DEMO_CASE})).structured_content or {}
        # Pick a legal, non-final next status so the demo can be rerun.
        allowed = [s for s in view.get("allowed_next_statuses", []) if not s.startswith("resolved")]
        if allowed:
            args = {"case_id": DEMO_CASE, "new_status": allowed[0], "reason": "demo transition"}
            await _call(client, "update_status", args)
        else:
            print(f"  (skipping update_status: {DEMO_CASE} is resolved; reseed with --reset)")
        bad = await _call(
            client,
            "update_status",
            {"case_id": DEMO_CASE, "new_status": "open", "reason": "illegal on purpose"},
        )
        errors += 0 if bad.is_error else 1
    return errors


async def main() -> int:
    started = datetime.now(UTC)
    unexpected = await demo_transactions() + await demo_merchant() + await demo_cases()

    with connect() as conn:
        rows = conn.execute(
            "SELECT server, outcome, count(*) FROM audit_log "
            "WHERE actor = %s AND created_at >= %s GROUP BY 1, 2 ORDER BY 1, 2",
            (CLIENT_NAME, started),
        ).fetchall()
    print("\n== audit_log rows written by this run")
    for server, outcome, count in rows:
        print(f"  {server:<13} {outcome:<14} {count}")

    if unexpected:
        print(f"\n{unexpected} invalid call(s) were accepted; expected rejection", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
