"""Helpers for driving MCP servers in-process from unit tests."""

from __future__ import annotations

from typing import Any

from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult, Implementation

from disputedesk.db.models import AuditEntry
from disputedesk.mcp_servers.common.store import InMemoryStore

TEST_ACTOR = "unit-test-agent"


async def call(
    server: MCPServer, tool: str, args: dict[str, Any], actor: str = TEST_ACTOR
) -> CallToolResult:
    async with Client(server, client_info=Implementation(name=actor, version="0")) as client:
        return await client.call_tool(tool, args)


async def list_tool_names(server: MCPServer) -> set[str]:
    async with Client(server) as client:
        return {t.name for t in (await client.list_tools()).tools}


def ok(result: CallToolResult) -> dict[str, Any]:
    """Assert success and return the structured result."""
    assert not result.is_error, _text(result)
    assert result.structured_content is not None
    return result.structured_content


def error_text(result: CallToolResult) -> str:
    assert result.is_error, "expected an error result"
    return _text(result)


def last_audit(store: InMemoryStore) -> AuditEntry:
    assert store.audit, "no audit entry written"
    return store.audit[-1]


def _text(result: CallToolResult) -> str:
    return " ".join(getattr(c, "text", "") for c in result.content)
