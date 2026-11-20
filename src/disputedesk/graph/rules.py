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
