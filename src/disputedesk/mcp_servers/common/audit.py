"""`AuditedMCPServer`: an MCPServer (formerly FastMCP) that writes one audit row per tool call.

The hook is `call_tool`, which runs before argument validation, so rejected calls (bad types,
unknown tools, unknown arguments) are audited too, not only successful ones.

Audit is fail-closed: if the audit row cannot be written, the call returns an error.
"""

from __future__ import annotations

import time
from typing import Any

import anyio.to_thread
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp_types import CallToolResult, InputRequiredResult
from pydantic import ValidationError

from disputedesk.db.models import AuditEntry, AuditOutcome
from disputedesk.mcp_servers.common.errors import (
    ConflictError,
    InvalidRequestError,
    NotFoundError,
)
from disputedesk.mcp_servers.common.params import Clock, utc_now
from disputedesk.mcp_servers.common.store import AuditSink

UNKNOWN_ACTOR = "unknown"
_MAX_ERROR_CHARS = 500


class AuditedMCPServer(MCPServer):
    def __init__(self, name: str, *, audit: AuditSink, clock: Clock = utc_now, **kwargs: Any):
        super().__init__(name, **kwargs)
        self._audit = audit
        self._clock = clock
        self._arg_names: dict[str, frozenset[str]] | None = None

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context[Any, Any] | None = None
    ) -> CallToolResult | InputRequiredResult:
        started = time.perf_counter()
        outcome, error = AuditOutcome.OK, None
        try:
            await self._reject_unknown_arguments(name, arguments)
            return await super().call_tool(name, arguments, context)
        except Exception as exc:
            outcome, error = classify(exc), describe(exc)
            raise
        finally:
            case_id = arguments.get("case_id")
            entry = AuditEntry(
                server=self.name or "unnamed",
                tool=name,
                actor=_actor(context),
                case_id=case_id if isinstance(case_id, str) else None,
                arguments=arguments,
                outcome=outcome,
                error=error,
                duration_ms=int((time.perf_counter() - started) * 1000),
                created_at=self._clock(),
            )
            await anyio.to_thread.run_sync(self._audit.write_audit, entry)

    async def _reject_unknown_arguments(self, name: str, arguments: dict[str, Any]) -> None:
        """The SDK silently drops unknown arguments; we refuse them so typos are visible."""
        if self._arg_names is None:
            self._arg_names = {
                t.name: frozenset(t.input_schema.get("properties", {}))
                for t in await self.list_tools()
            }
        allowed = self._arg_names.get(name)
        if allowed is None:
            return  # unknown tool: the SDK raises its own error
        extra = sorted(set(arguments) - allowed)
        if extra:
            raise InvalidRequestError(f"unknown argument(s) for {name}: {', '.join(extra)}")


def classify(exc: BaseException) -> AuditOutcome:
    """Map a tool exception to an audit outcome.

    The SDK re-wraps errors raised inside a tool (original in `__cause__`), so check both.
    """
    for err in (exc, exc.__cause__):
        if isinstance(err, NotFoundError):
            return AuditOutcome.NOT_FOUND
        if isinstance(err, ConflictError):
            return AuditOutcome.CONFLICT
        if isinstance(err, InvalidRequestError | ValidationError):
            return AuditOutcome.INVALID_INPUT
    if isinstance(exc, UnexpectedToolError):
        return AuditOutcome.ERROR
    if isinstance(exc, ToolError) and "Unknown tool" in str(exc):
        return AuditOutcome.INVALID_INPUT
    return AuditOutcome.ERROR


def describe(exc: BaseException) -> str:
    """Error text for the audit row. Crash details stay server-side (the client sees less)."""
    text = str(exc)
    if isinstance(exc, UnexpectedToolError) and exc.__cause__ is not None:
        text = f"{text}: {exc.__cause__!r}"
    return text[:_MAX_ERROR_CHARS]


def _actor(context: Context[Any, Any] | None) -> str:
    """Caller identity: the MCP client name for now. Replaced by verified identity in M5."""
    if context is None:
        return UNKNOWN_ACTOR
    try:
        params = context.session.client_params
    except ValueError:  # no request context (direct in-process call)
        return UNKNOWN_ACTOR
    if params is None:
        return UNKNOWN_ACTOR
    return params.client_info.name or UNKNOWN_ACTOR
