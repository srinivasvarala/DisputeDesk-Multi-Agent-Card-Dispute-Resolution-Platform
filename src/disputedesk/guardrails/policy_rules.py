"""Deterministic guardrail rules: tool allowlists and the refund cap.

Plain Python on purpose. Limits and allowlists are never decided by an LLM or by NeMo.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from disputedesk.agents.allowlists import ALLOWLISTS

# Arguments known to hold a refund amount in cents.
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


def _to_cents(value: object) -> int:
    """Read a cents value (int, float, Decimal or numeric string) or raise ValueError."""
    if isinstance(value, bool):
        raise ValueError("boolean is not an amount")
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError(f"not a number: {value!r}") from exc
    if not number.is_finite() or number < 0:
        raise ValueError(f"not a valid amount: {value!r}")
    return int(number.to_integral_value(rounding=ROUND_CEILING))  # round up: never under-count


def refund_amount(arguments: Mapping[str, Any]) -> int | None:
    """The largest refund amount in the arguments, or None if there is none.

    Raises ValueError when an amount is present but unreadable (wrong type, negative) or sits
    in a key whose unit we do not know (e.g. `amount`): the caller must fail closed.
    """
    amounts: list[int] = []
    for key, value in arguments.items():
        if key in REFUND_ARGUMENTS:
            amounts.append(_to_cents(value))
        elif "refund" in key.lower() or "amount" in key.lower():
            raise ValueError(f"unrecognised amount argument {key!r}: unit unknown")
    return max(amounts) if amounts else None


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
    try:
        amount = refund_amount(arguments)
    except ValueError as exc:  # cannot tell how much money is involved: a human decides
        return RuleResult(action=RuleAction.REQUIRE_APPROVAL, reasons=[str(exc)])
    if amount is not None and refund_needs_approval(amount, cap_cents):
        return RuleResult(
            action=RuleAction.REQUIRE_APPROVAL,
            reasons=[f"refund {amount} cents is at or above the {cap_cents} cent cap"],
        )
    return RuleResult(action=RuleAction.ALLOW)
