"""Helpers shared by the LLM agents: untrusted-text wrapping, structured (JSON) output with one
validation retry, and decision auditing."""

from __future__ import annotations

import html
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from disputedesk.db.models import AuditEntry, AuditOutcome
from disputedesk.llm import LLMClient, LLMError, Message
from disputedesk.mcp_servers.common.store import AuditSink

AUDIT_SERVER = "agents"
MAX_ATTEMPTS = 2
# Roomy on purpose: a truncated reply is a crashed case. Haiku 5.5 spends hidden reasoning tokens
# against max_tokens (a 450-token JSON answer used about 1,750), so 1024 failed on 20 of 40 golden
# cases and 2048 still failed on 5 (M7 evals). Only tokens actually used are billed.
DEFAULT_MAX_TOKENS = 8192


class AgentOutputError(RuntimeError):
    """The model did not produce a usable structured answer."""


UNTRUSTED_LABEL = 'trust="untrusted"'


def untrusted(tag: str, text: str) -> str:
    """Wrap customer/merchant-derived text as labelled, escaped data.

    - Labelled: the opening tag carries `trust="untrusted"`, which every system prompt tells the
      model to treat as data, never as instructions.
    - Escaped: `<`, `>`, `&` and quotes are escaped, so the text cannot close the tag or open a
      new one to impersonate trusted content.
    - Only ever placed in the user message, never in the system prompt.
    """
    return f"<{tag} {UNTRUSTED_LABEL}>\n{html.escape(text)}\n</{tag}>"


def complete_json[T: BaseModel](
    llm: LLMClient,
    *,
    system: str,
    user: str,
    schema: type[T],
    max_tokens: int = DEFAULT_MAX_TOKENS,
    check: Callable[[T], None] | None = None,
) -> T:
    """Ask for one JSON object matching `schema`. On bad output, retry once with the error.

    `check` is for rules the schema cannot express; it raises ValueError to reject the answer.
    """
    messages = [Message(role="user", content=user)]
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            reply = llm.complete(system=system, messages=messages, max_tokens=max_tokens)
        except LLMError as exc:
            raise AgentOutputError(f"LLM call failed: {exc}") from exc
        try:
            value = _parse(reply.text, schema)
            if check is not None:
                check(value)
            return value
        except ValueError as exc:
            if attempt == MAX_ATTEMPTS:
                raise AgentOutputError(f"invalid answer after {attempt} attempts: {exc}") from exc
            messages += [
                Message(role="assistant", content=reply.text),
                Message(
                    role="user",
                    content=f"Your reply was rejected: {exc}. Reply again with the JSON object "
                    "only, matching the requested shape.",
                ),
            ]
    raise AssertionError("unreachable")


def _parse[T: BaseModel](text: str, schema: type[T]) -> T:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("reply is not a JSON object")
    try:
        return schema.model_validate(json.loads(text[start : end + 1]))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"reply does not match the schema: {exc}") from exc


class DecisionAuditor:
    """Writes one audit row per agent decision. Fails closed, like the MCP servers."""

    def __init__(
        self, sink: AuditSink, actor: str, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self._sink = sink
        self._actor = actor
        self._clock = clock

    def record(
        self,
        tool: str,
        case_id: str,
        arguments: dict[str, Any],
        *,
        outcome: AuditOutcome = AuditOutcome.OK,
        error: str | None = None,
        started: float | None = None,
    ) -> None:
        duration = 0 if started is None else int((time.monotonic() - started) * 1000)
        self._sink.write_audit(
            AuditEntry(
                server=AUDIT_SERVER,
                tool=tool,
                actor=self._actor,
                case_id=case_id,
                arguments=arguments,
                outcome=outcome,
                error=error,
                duration_ms=duration,
                created_at=self._clock(),
            )
        )

    def record_failure(
        self,
        tool: str,
        case_id: str,
        arguments: dict[str, Any],
        exc: BaseException,
        *,
        started: float | None = None,
    ) -> None:
        """Audit a failed decision. Only the exception TYPE is stored.

        The audit log is append-only, and an agent error message can carry the model's reply
        (a schema error echoes the offending value), which can echo the cardholder's narrative.
        The detail is in the application log, which the caller writes when it re-raises.
        """
        self.record(
            tool,
            case_id,
            arguments,
            outcome=AuditOutcome.ERROR,
            error=type(exc).__name__,
            started=started,
        )
