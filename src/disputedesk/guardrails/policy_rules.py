"""Deterministic guardrail rules: tool allowlists and the refund cap.

Plain Python on purpose. Limits and allowlists are never decided by an LLM or by NeMo.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from disputedesk.agents.allowlists import ALLOWLISTS

# Tool arguments that carry a refund amount in cents. An accepted dispute refunds money, so
# any tool call that states an amount is checked against the cap.
REFUND_ARGUMENTS = ("refund_cents", "amount_cents")


class RuleAction(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class RuleResult(BaseModel):
    action: RuleAction
    reasons: list[str] = []


def tool_is_allowed(agent: str, tool: str, allowed: frozenset[str] | None = None) -> bool:
    """Is `tool` on the agent's allowlist? An unknown agent has no tools."""
    permitted = allowed if allowed is not None else ALLOWLISTS.get(agent, frozenset())
    return tool in permitted


def refund_needs_approval(refund_cents: int, cap_cents: int) -> bool:
    """At or above the cap a human must approve. Same boundary as `graph.rules.risk_tier`."""
    return refund_cents >= cap_cents


def refund_amount(arguments: Mapping[str, Any]) -> int | None:
    for key in REFUND_ARGUMENTS:
        value = arguments.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def evaluate_tool_call(
    agent: str,
    tool: str,
    arguments: Mapping[str, Any],
    *,
    cap_cents: int,
    allowed: frozenset[str] | None = None,
) -> RuleResult:
    """Allowlist first (deny wins), then the refund cap."""
    if not tool_is_allowed(agent, tool, allowed):
        return RuleResult(action=RuleAction.DENY, reasons=[f"{agent} may not call {tool}"])
    amount = refund_amount(arguments)
    if amount is not None and refund_needs_approval(amount, cap_cents):
        return RuleResult(
            action=RuleAction.REQUIRE_APPROVAL,
            reasons=[f"refund {amount} cents is at or above the {cap_cents} cent cap"],
        )
    return RuleResult(action=RuleAction.ALLOW)
