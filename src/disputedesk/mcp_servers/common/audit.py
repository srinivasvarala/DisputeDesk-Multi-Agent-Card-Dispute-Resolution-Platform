"""`AuditedMCPServer`: an MCPServer (formerly FastMCP) that writes one audit row per tool call.

The hook is `call_tool`, which runs before argument validation, so rejected calls (bad types,
unknown tools, unknown arguments) are audited too, not only successful ones.

Rules:
- Exactly one row per call: ok, invalid_input, not_found, conflict, error or cancelled.
- Write tools store their ok row in the same transaction as the change (`audited_write`), so a
  change is never committed without its audit row.
- Fail-closed: if the row cannot be written, the call returns an error. The client gets a
  generic message; database details are only logged server-side.
- A `verifier` is required (`NO_AUTH` is the explicit opt-out). With a `CredentialVerifier`,
  every call must carry a valid agent credential that grants the tool. The audit actor is then
  the verified agent, never the client's self-declared name. A refused call is audited as
  `denied` and never reaches the tool.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import anyio
import anyio.to_thread
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp_types import CallToolResult, InputRequiredResult
from pydantic import ValidationError

from disputedesk.db.models import AuditEntry, AuditOutcome
from disputedesk.mcp_servers.common.errors import (
    AuditUnavailableError,
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    PermissionDeniedError,
)
from disputedesk.mcp_servers.common.params import Clock, utc_now
from disputedesk.mcp_servers.common.store import AuditSink
from disputedesk.security.agent_tokens import META_KEY, CredentialVerifier, NoAuth
from disputedesk.security.jwt import AuthError

logger = logging.getLogger(__name__)

UNKNOWN_ACTOR = "unknown"
_MAX_ERROR_CHARS = 500


@dataclass
class _CallAudit:
    """What we know about the tool call in progress."""

    server: str
    tool: str
    actor: str
    arguments: dict[str, Any]
    clock: Clock
    started: float
    recorded: bool = False

    def entry(self, outcome: AuditOutcome, error: str | None) -> AuditEntry:
        case_id = self.arguments.get("case_id")
        return AuditEntry(
            server=self.server,
            tool=self.tool,
            actor=self.actor,
            case_id=case_id if isinstance(case_id, str) else None,
            arguments=self.arguments,
            outcome=outcome,
            error=error,
            duration_ms=int((time.perf_counter() - self.started) * 1000),
            created_at=self.clock(),
        )


# Set per call by AuditedMCPServer; anyio copies context into the worker thread a sync tool
# runs on, so write tools can reach it.
_current_call: ContextVar[_CallAudit | None] = ContextVar("current_call_audit", default=None)


def audited_write[T](write: Callable[[AuditEntry], T]) -> T:
    """Run a store write that persists the call's ok audit row in its own transaction.

    Usage inside a write tool: `audited_write(lambda audit: store.add_draft(..., audit=audit))`.
    Only after the write succeeds is the call marked as recorded, so a failed write still gets
    an error row from AuditedMCPServer.
    """
    call = _current_call.get()
    if call is None:
        raise RuntimeError("audited_write used outside an AuditedMCPServer tool call")
    result = write(call.entry(AuditOutcome.OK, None))
    call.recorded = True
    return result


class AuditedMCPServer(MCPServer):
    def __init__(
        self,
        name: str,
        *,
        audit: AuditSink,
        clock: Clock = utc_now,
        verifier: CredentialVerifier | NoAuth,
        **kwargs: Any,
    ):
        super().__init__(name, **kwargs)
        self._audit = audit
        self._clock = clock
        self._verifier = verifier
        self._arg_names: dict[str, frozenset[str]] | None = None

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context[Any, Any] | None = None
    ) -> CallToolResult | InputRequiredResult:
        call = _CallAudit(
            server=self.name or "unnamed",
            tool=name,
            actor=_actor(context),
            arguments=arguments,
            clock=self._clock,
            started=time.perf_counter(),
        )
        token = _current_call.set(call)
        outcome, error, cancelled = AuditOutcome.OK, None, False
        try:
            self._authorize(call, context)
            await self._reject_unknown_arguments(name, arguments)
            return await super().call_tool(name, arguments, context)
        except anyio.get_cancelled_exc_class():
            outcome, error, cancelled = AuditOutcome.CANCELLED, "call cancelled", True
            raise
        except Exception as exc:
            outcome, error = classify(exc), describe(exc)
            raise
        finally:
            _current_call.reset(token)
            if not call.recorded:
                await self._write(call.entry(outcome, error), cancelled=cancelled)

    def _authorize(self, call: _CallAudit, context: Context[Any, Any] | None) -> None:
        """Check the agent credential. Skipped only for an explicit `NO_AUTH` server."""
        if isinstance(self._verifier, NoAuth):
            return
        call.actor = f"unauthenticated:{call.actor}"  # until proven otherwise
        try:
            identity = self._verifier.verify(_credential(context))
        except AuthError:
            raise PermissionDeniedError("missing or invalid credential") from None
        call.actor = identity.agent
        if call.tool not in identity.tools:
            raise PermissionDeniedError(f"{identity.agent} is not granted {call.tool}")

    async def _write(self, entry: AuditEntry, *, cancelled: bool) -> None:
        try:
            # Shielded so a cancelled call still gets its audit row.
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(self._audit.write_audit, entry)
        except Exception:
            logger.exception("audit write failed for %s.%s", entry.server, entry.tool)
            if not cancelled:  # never mask a cancellation
                raise AuditUnavailableError("audit log unavailable; the call was refused") from None

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


def _credential(context: Context[Any, Any] | None) -> object:
    if context is None:
        return None
    try:
        meta = context.request_context.meta
    except ValueError:
        return None
    return meta.get(META_KEY) if isinstance(meta, dict) else None


def classify(exc: BaseException) -> AuditOutcome:
    """Map a tool exception to an audit outcome.

    The SDK re-wraps errors raised inside a tool (original in `__cause__`), so check both.
    """
    for err in (exc, exc.__cause__):
        if isinstance(err, NotFoundError):
            return AuditOutcome.NOT_FOUND
        if isinstance(err, ConflictError):
            return AuditOutcome.CONFLICT
        if isinstance(err, PermissionDeniedError):
            return AuditOutcome.DENIED
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
    """The client's self-declared name. Only trusted on a `NO_AUTH` server."""
    if context is None:
        return UNKNOWN_ACTOR
    try:
        params = context.session.client_params
    except ValueError:  # no request context (direct in-process call)
        return UNKNOWN_ACTOR
    if params is None:
        return UNKNOWN_ACTOR
    return params.client_info.name or UNKNOWN_ACTOR
