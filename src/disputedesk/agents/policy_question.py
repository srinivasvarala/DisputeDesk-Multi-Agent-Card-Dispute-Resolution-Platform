"""Builds the question the Policy agent answers for a case.

It is made only from structured, trusted fields (reason code, amount, merchant category,
risk tier). The cardholder's narrative and the Evidence agent's free text are left out, so no
untrusted text reaches the retriever query.
"""

from __future__ import annotations

from disputedesk.graph.state import CaseFacts, Evidence, Route


def policy_question(facts: CaseFacts, route: Route, evidence: Evidence) -> str:
    dollars = facts.amount_cents / 100
    parts = [
        f"What does the DisputeDesk Rulebook say about handling a {route.reason_code} dispute "
        f"for a ${dollars:,.2f} {evidence.transaction.channel} charge at a "
        f"{evidence.merchant.category} merchant?",
        "Include the evidence standard, the refund rules, and whether the approval threshold "
        "or an escalation trigger applies.",
    ]
    if route.approval_required:
        parts.append("The case is high risk and needs human approval.")
    return " ".join(parts)
