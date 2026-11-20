"""Builds the question the Policy agent answers for a case.

It is made only from structured, trusted fields (reason code, amount, channel). The cardholder's
narrative and the Evidence agent's free text are left out, so no untrusted text reaches the
retriever query.

The question names the rulebook topics of the reason code (for example "delivery proof" for
R20). A generic question ("evidence standard, refund rules, approval") matched the overview and
approval documents instead of the reason-code rules, and the Resolution agent then escalated
nearly every case (M7 baseline: decision accuracy 0.133). One short tail clause still pulls in
the approval and escalation rules, which apply to every reason code.
"""

from __future__ import annotations

from disputedesk.db.models import Channel, ReasonCode
from disputedesk.graph.state import CaseFacts, Evidence, Route

TAIL = (
    " Also: the approval threshold, mandatory escalation triggers and repeat-dispute cardholders."
)


def _topic(reason: ReasonCode, channel: Channel) -> str:
    match reason:
        case ReasonCode.FRAUD if channel == Channel.CARD_PRESENT:
            return (
                "Card-Present Fraud (F11) rules: chip and PIN, magnetic stripe fallback, "
                "lost or stolen cards and the decision rules for accept or reject"
            )
        case ReasonCode.FRAUD:
            return (
                "Card-Absent Fraud (F10) rules: cardholder requirements, compelling evidence "
                "of participation and the decision rules for accept or reject"
            )
        case ReasonCode.NOT_RECEIVED:
            return (
                "Merchandise Not Received (R20) rules: delivery proof, signature confirmation, "
                "address mismatch and the decision rules for accept or reject"
            )
        case ReasonCode.DUPLICATE:
            return (
                "Duplicate Processing (P30) rules: when two charges count as duplicates, "
                "merchant evidence and the decision rules for accept or reject"
            )
        case ReasonCode.NOT_AS_DESCRIBED:
            return (
                "Not as Described (Q40 to Q42) rules: what does not qualify, the contact and "
                "return requirements and the decision rules for accept or reject"
            )


def policy_question(facts: CaseFacts, route: Route, evidence: Evidence) -> str:
    dollars = facts.amount_cents / 100
    question = (
        f"What are the {_topic(route.reason_code, evidence.transaction.channel)} for a "
        f"{route.reason_code} dispute on a ${dollars:,.2f} {evidence.transaction.channel} "
        f"charge at a {evidence.merchant.category} merchant?"
    )
    if route.approval_required:
        question += " The case is high risk and needs human approval."
    return question + TAIL
