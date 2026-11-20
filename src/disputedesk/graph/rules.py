"""Deterministic business rules for routing. Plain Python: never decided by an LLM."""

from __future__ import annotations

from disputedesk.db.models import ReasonCode, RiskTier


def risk_tier(
    *, amount_cents: int, merchant_risk: RiskTier, reason: ReasonCode, threshold_cents: int
) -> RiskTier:
    """HIGH: a high-risk merchant or an amount at/above the approval threshold.
    MEDIUM: a medium-risk merchant, or any fraud claim. Otherwise LOW."""
    if merchant_risk == RiskTier.HIGH or amount_cents >= threshold_cents:
        return RiskTier.HIGH
    if merchant_risk == RiskTier.MEDIUM or reason == ReasonCode.FRAUD:
        return RiskTier.MEDIUM
    return RiskTier.LOW


def approval_required(tier: RiskTier) -> bool:
    return tier == RiskTier.HIGH


CONFIDENCE_FLOOR = 0.70  # rulebook 11, trigger 2
REPEAT_DISPUTE_LIMIT = 3  # rulebook 11, trigger 5: disputes in the last 90 days, this one included
HISTORY_DAYS = 90


def escalation_override(*, confidence: float, disputes_90d: int) -> str | None:
    """Escalation triggers the analyst cannot be trusted to apply to itself (rulebook 11).

    Returns the reason to escalate, or None. Plain Python: the model reports a confidence, but
    what that number means for the case is decided here.
    """
    if disputes_90d >= REPEAT_DISPUTE_LIMIT:
        return (
            f"cardholder has {disputes_90d} disputes in the last {HISTORY_DAYS} days "
            f"(rulebook 11, trigger 5)"
        )
    if confidence < CONFIDENCE_FLOOR:
        return (
            f"analyst confidence {confidence:.2f} is below {CONFIDENCE_FLOOR} "
            f"(rulebook 11, trigger 2)"
        )
    return None
