"""Case status rules. Deterministic plain Python: never decided by an LLM.

    open ──> in_review ──> pending_approval ──> resolved_accepted / resolved_rejected
      │          │  ▲             │
      │          │  └─────────────┘ (sent back)
      └──────────┴──> escalated ──> in_review / resolved_*

Resolving a case, or sending it for approval, requires at least one saved draft.
"""

from __future__ import annotations

from disputedesk.db.models import CaseStatus

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
NEEDS_DRAFT = frozenset({S.PENDING_APPROVAL, S.RESOLVED_ACCEPTED, S.RESOLVED_REJECTED})


def transition_error(current: CaseStatus, new: CaseStatus, *, has_draft: bool) -> str | None:
    """Return why `current -> new` is not allowed, or None if it is."""
    if new not in ALLOWED_TRANSITIONS[current]:
        allowed = ", ".join(sorted(ALLOWED_TRANSITIONS[current])) or "none (terminal)"
        return f"cannot move case from {current} to {new}; allowed: {allowed}"
    if new in NEEDS_DRAFT and not has_draft:
        return f"cannot move case to {new} without a saved draft"
    return None
