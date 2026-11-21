"""Deterministic guardrail rules for tool calls: the per-agent tool allowlist.

Plain Python on purpose. Limits and allowlists are never decided by an LLM or by NeMo.

There used to be a refund-cap rule here. No tool carries a refund amount (nothing is paid out),
so it could never fire; the control that holds back large amounts is the risk tier in
`graph/rules.py`, which routes them to a human approver. Rules about tool *arguments* belong in
`evaluate_tool_call` when a tool exists that needs them.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from disputedesk.agents.allowlists import ALLOWLISTS


class RuleAction(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class RuleResult(BaseModel):
    action: RuleAction
    reasons: list[str] = []


def tool_is_allowed(agent: str, tool: str, allowed: frozenset[str] | None = None) -> bool:
    """Is `tool` on the agent's allowlist? An unknown agent has no tools."""
    permitted = allowed if allowed is not None else ALLOWLISTS.get(agent, frozenset())
    return tool in permitted


def evaluate_tool_call(
    agent: str,
    tool: str,
    *,
    allowed: frozenset[str] | None = None,
) -> RuleResult:
    """Deny a tool that is not on the agent's allowlist; allow the rest."""
    if not tool_is_allowed(agent, tool, allowed):
        return RuleResult(action=RuleAction.DENY, reasons=[f"{agent} may not call {tool}"])
    return RuleResult(action=RuleAction.ALLOW)
