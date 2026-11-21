"""The one door for guardrail checks: `check_input`, `check_output`, `check_tool_call`.

- Edges (input, output) use Presidio redaction plus NeMo rails (`nemo_edge.py`).
- Tool calls use plain-Python rules only (`policy_rules.py`). No NeMo, no LLM.
- Every decision is emitted as trace attributes and written to the audit log. The audit row
  holds rail names and entity types, never the text itself.
- Fail closed: if a NeMo check errors, the text is blocked.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from functools import lru_cache
from typing import Any

from pydantic import BaseModel

from disputedesk.config import get_settings
from disputedesk.db.models import AuditEntry, AuditOutcome
from disputedesk.guardrails.judges import LLMJudge
from disputedesk.guardrails.nemo_edge import NemoEdge
from disputedesk.guardrails.policy_rules import RuleAction, evaluate_tool_call, refund_amount
from disputedesk.guardrails.redaction import get_redactor
from disputedesk.guardrails.telemetry import emit_guardrail_event
from disputedesk.llm import get_llm
from disputedesk.mcp_servers.common.store import AuditSink

log = logging.getLogger(__name__)

AUDIT_SERVER = "guardrails"


class Action(StrEnum):
    ALLOW = "allow"
    REDACT = "redact"  # allowed, but `text` is the redacted version: use it, not the original
    BLOCK = "block"  # edge text rejected by a rail
    DENY = "deny"  # tool call refused
    REQUIRE_APPROVAL = "require_approval"  # a human approver must decide first


class Verdict(BaseModel):
    action: Action
    rail: str | None = None
    reasons: list[str] = []
    text: str | None = None  # input/output checks: the text to pass on (redacted if needed)

    @property
    def allowed(self) -> bool:
        return self.action in (Action.ALLOW, Action.REDACT)


@lru_cache(maxsize=1)
def _edge() -> NemoEdge:
    """Built on first use: loading NeMo and spaCy takes seconds; tool checks need neither."""
    settings = get_settings()
    judge = LLMJudge(get_llm(settings)) if settings.guardrail_judge else None
    return NemoEdge(get_redactor(), judge)


_OUTCOME = {
    Action.ALLOW: AuditOutcome.OK,
    Action.REDACT: AuditOutcome.OK,
    Action.BLOCK: AuditOutcome.INVALID_INPUT,
    Action.DENY: AuditOutcome.INVALID_INPUT,
    Action.REQUIRE_APPROVAL: AuditOutcome.APPROVAL_REQUIRED,
}


def _record(
    verdict: Verdict,
    *,
    check: str,
    agent: str,
    case_id: str | None,
    audit: AuditSink | None,
    detail: Mapping[str, Any] | None = None,
    audit_allow: bool = True,
) -> Verdict:
    triggered = verdict.action != Action.ALLOW
    rail = verdict.rail or check
    emit_guardrail_event(
        rail=rail,
        action=verdict.action,
        agent=agent,
        case_id=case_id,
        triggered=triggered,
        reason="; ".join(verdict.reasons),
    )
    if audit is not None and (audit_allow or verdict.action != Action.ALLOW):
        audit.write_audit(
            AuditEntry(
                server=AUDIT_SERVER,
                tool=check,
                actor=agent,
                case_id=case_id,
                arguments={"rail": rail, "action": verdict.action, **(detail or {})},
                outcome=_OUTCOME[verdict.action],
                error="; ".join(verdict.reasons) or None,
                duration_ms=0,
                created_at=datetime.now(UTC),
            )
        )
    return verdict


def check_input(
    text: str, *, agent: str, case_id: str | None = None, audit: AuditSink | None = None
) -> Verdict:
    """Redact PII, then run the jailbreak/injection and off-topic rails on the redacted text."""
    redacted, entities = get_redactor().redact(text)
    try:
        edge = _edge().check_input(redacted)
    except Exception:
        log.exception("NeMo input check failed; blocking")
        verdict = Verdict(action=Action.BLOCK, rail="nemo_error", reasons=["guardrail error"])
    else:
        if edge.blocked:
            verdict = Verdict(
                action=Action.BLOCK, rail=edge.rail, reasons=[f"blocked by {edge.rail}"]
            )
        elif entities:
            verdict = Verdict(action=Action.REDACT, rail="pii_redaction", reasons=[], text=redacted)
        else:
            verdict = Verdict(action=Action.ALLOW, text=text)
    return _record(
        verdict,
        check="check_input",
        agent=agent,
        case_id=case_id,
        audit=audit,
        detail={"entities": entities},
    )


def check_output(
    text: str,
    *,
    agent: str,
    case_id: str | None = None,
    grounding: Sequence[str] | None = None,
    audit: AuditSink | None = None,
) -> Verdict:
    """Output rails: PII (redacted, not blocked) and grounding (blocked).

    `grounding` is the case material the text may quote figures from (evidence, policy excerpts,
    case facts). Without it the grounding rail has nothing to check against and passes.
    """
    entities: list[str] = []
    try:
        edge = _edge().check_output(text, grounding)
        if edge.blocked and edge.rail == "check output pii":
            # PII is fixable: redact it, then run the rails again on what is left.
            text, entities = get_redactor().redact(text)
            edge = _edge().check_output(text, grounding)
    except Exception:
        log.exception("NeMo output check failed; blocking")
        verdict = Verdict(action=Action.BLOCK, rail="nemo_error", reasons=["guardrail error"])
    else:
        if edge.blocked:
            verdict = Verdict(
                action=Action.BLOCK, rail=edge.rail, reasons=[f"blocked by {edge.rail}"]
            )
        elif entities:
            verdict = Verdict(action=Action.REDACT, rail="check output pii", text=text)
        else:
            verdict = Verdict(action=Action.ALLOW, text=text)
    return _record(
        verdict,
        check="check_output",
        agent=agent,
        case_id=case_id,
        audit=audit,
        detail={"entities": entities},
    )


def _amount(arguments: Mapping[str, Any]) -> int | None:
    try:
        return refund_amount(arguments)
    except ValueError:
        return None


def check_tool_call(
    agent: str,
    tool: str,
    arguments: Mapping[str, Any],
    *,
    case_id: str | None = None,
    allowed: frozenset[str] | None = None,
    audit: AuditSink | None = None,
) -> Verdict:
    """Deterministic: the agent's tool allowlist, then the refund cap.

    `allowed` overrides the default allowlist for the agent (a `ToolBox` passes its own).
    """
    result = evaluate_tool_call(
        agent,
        tool,
        arguments,
        cap_cents=get_settings().human_approval_threshold_cents,
        allowed=allowed,
    )
    action = {
        RuleAction.ALLOW: Action.ALLOW,
        RuleAction.DENY: Action.DENY,
        RuleAction.REQUIRE_APPROVAL: Action.REQUIRE_APPROVAL,
    }[result.action]
    verdict = Verdict(action=action, rail="policy_rules", reasons=result.reasons)
    return _record(
        verdict,
        check=tool,
        agent=agent,
        case_id=case_id,
        audit=audit,
        # Argument names and the refund amount only: values (letters, evidence) may hold PII.
        detail={
            "tool": tool,
            "argument_names": sorted(arguments),
            "refund_cents": _amount(arguments),
        },
        audit_allow=False,  # allowed calls are audited by the MCP servers themselves
    )
