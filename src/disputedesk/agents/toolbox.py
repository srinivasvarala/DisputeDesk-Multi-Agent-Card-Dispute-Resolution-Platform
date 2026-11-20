"""Per-agent tool access. An agent gets a `ToolBox` built from its allowlist and nothing else.

Tools are the MCP servers' tools, called in-process through a real MCP client whose name is the
agent's, so the servers' own audit rows show which agent made each call. A call outside the
allowlist is refused before it reaches any server, and the refusal is audited too.

With an `issuer`, each call carries a short-lived credential naming the agent and its allowed
tools; the servers verify it, so the allowlist is enforced on both sides of the boundary.

Sync API: call it from a thread without a running event loop (graph nodes run in one).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

import anyio
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp_types import Implementation, RequestParamsMeta

from disputedesk.agents.resilience import CallFailedError, NonRetryableError, call_with_retry
from disputedesk.guardrails.interface import Action, check_tool_call
from disputedesk.mcp_servers.common.store import AuditSink
from disputedesk.security.agent_tokens import META_KEY, CredentialIssuer


class ToolDeniedError(PermissionError):
    """The agent tried a tool that is not on its allowlist."""


class ApprovalRequiredError(PermissionError):
    """The call is allowed in principle but a human approver must decide first (refund cap)."""


class ToolCallError(NonRetryableError, RuntimeError):
    """The server answered with an error (not found, conflict, invalid input...). Retrying
    the same request cannot help."""


class ToolBox:
    def __init__(
        self,
        *,
        agent: str,
        allowed: frozenset[str],
        tools: Mapping[str, MCPServer],
        audit: AuditSink,
        timeout: float,
        retries: int,
        issuer: CredentialIssuer | None = None,
    ) -> None:
        self.agent = agent
        self.allowed = allowed
        self._tools = tools  # tool name -> the server that provides it (see `tool_index`)
        self._audit = audit
        self._timeout = timeout
        self._retries = retries
        self._issuer = issuer

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run an allowed tool and return its structured result."""
        case_id = arguments.get("case_id")
        verdict = check_tool_call(
            self.agent,
            tool,
            arguments,
            case_id=case_id if isinstance(case_id, str) else None,
            allowed=self.allowed,
            audit=self._audit,
        )
        if verdict.action == Action.DENY:
            raise ToolDeniedError(f"{self.agent} may not call {tool}")
        if verdict.action == Action.REQUIRE_APPROVAL:
            raise ApprovalRequiredError("; ".join(verdict.reasons))
        server = self._tools.get(tool)
        if server is None:
            raise ToolCallError(f"no server provides tool {tool}")
        try:
            return call_with_retry(
                lambda: asyncio.run(self._call(server, tool, arguments)),
                name=f"tool:{tool}",
                timeout=self._timeout,
                retries=self._retries,
            )
        except CallFailedError as exc:
            raise ToolCallError(str(exc)) from exc

    async def _call(
        self, server: MCPServer, tool: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        info = Implementation(name=self.agent, version="0")
        with anyio.fail_after(self._timeout):
            async with Client(server, client_info=info) as client:
                result = await client.call_tool(tool, arguments, meta=self._credential_meta())
        if result.is_error:
            text = " ".join(getattr(c, "text", "") for c in result.content)
            raise ToolCallError(f"{tool} failed: {text}")
        return result.structured_content or {}

    def _credential_meta(self) -> RequestParamsMeta | None:
        if self._issuer is None:
            return None
        return cast(RequestParamsMeta, {META_KEY: self._issuer.issue(self.agent, self.allowed)})


def tool_index(servers: Iterable[MCPServer]) -> dict[str, MCPServer]:
    """Map each tool name to the server that provides it.

    Safe to call from inside a running event loop (e.g. app startup): the async lookup runs on
    its own thread, because `asyncio.run` refuses to start in a thread that already has a loop.
    """

    async def names(server: MCPServer) -> list[str]:
        return [t.name for t in await server.list_tools()]

    index: dict[str, MCPServer] = {}
    with ThreadPoolExecutor(max_workers=1) as pool:
        for server in servers:
            for name in pool.submit(asyncio.run, names(server)).result():
                if name in index:
                    raise ValueError(f"tool name {name} is provided by two servers")
                index[name] = server
    return index
