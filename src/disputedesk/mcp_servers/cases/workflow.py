"""Case status rules. Deterministic plain Python: never decided by an LLM.

    open ──> in_review ──> pending_approval ──> resolved_accepted / resolved_rejected
      │          │  ▲             │
      │          │  └─────────────┘ (sent back)
      └──────────┴──> escalated ──> in_review / resolved_*

Draft rules (the latest draft is the one that counts):
- pending_approval needs a latest draft deciding accept or reject (escalate -> use escalated).
- resolved_accepted needs latest decision accept; resolved_rejected needs reject.
A human override of the draft is a separate, explicit path (M3/M5), not this tool.
"""

from __future__ import annotations

from disputedesk.db.models import CaseStatus, Decision

S = CaseStatus

ALLOWED_TRANSITIONS: dict[CaseStatus, frozenset[CaseStatus]] = {
    S.OPEN: frozenset({S.IN_REVIEW, S.ESCALATED}),
    S.IN_REVIEW: frozenset(
        {S.PENDING_APPROVAL, S.RESOLVED_ACCEPTED, S.RESOLVED_REJECTED, S.ESCALATED}
    ),
    S.PENDING_APPROVAL: frozenset(
        {S.IN_REVIEW, S.RESOLVED_ACCEPTED, S.RESOLVED_REJECTED, S.ESCALATED}
    ),
    S.ESCALATED: frozenset({S.IN_REVIEW, S.RESOLVED_ACCEPTED, S.RESOLVED_REJECTED}),
    S.RESOLVED_ACCEPTED: frozenset(),
    S.RESOLVED_REJECTED: frozenset(),
}

TERMINAL = frozenset(s for s, nxt in ALLOWED_TRANSITIONS.items() if not nxt)
REQUIRED_DECISION: dict[CaseStatus, frozenset[Decision]] = {
    S.PENDING_APPROVAL: frozenset({Decision.ACCEPT, Decision.REJECT}),
    S.RESOLVED_ACCEPTED: frozenset({Decision.ACCEPT}),
    S.RESOLVED_REJECTED: frozenset({Decision.REJECT}),
}


def transition_error(
    current: CaseStatus, new: CaseStatus, *, latest_decision: Decision | None
) -> str | None:
    """Return why `current -> new` is not allowed, or None if it is."""
    if new not in ALLOWED_TRANSITIONS[current]:
        allowed = ", ".join(sorted(ALLOWED_TRANSITIONS[current])) or "none (terminal)"
        return f"cannot move case from {current} to {new}; allowed: {allowed}"
    required = REQUIRED_DECISION.get(new)
    if required is None:
        return None
    if latest_decision is None:
        return f"cannot move case to {new} without a saved draft"
    if latest_decision not in required:
        wanted = " or ".join(sorted(required))
        return (
            f"cannot move case to {new}: latest draft decision is {latest_decision}, "
            f"needs {wanted}; save a matching draft first"
        )
    return None
