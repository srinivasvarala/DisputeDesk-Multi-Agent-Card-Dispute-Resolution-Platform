"""Conditional-edge decisions. Pure functions of the state: deterministic, never LLM-based."""

from __future__ import annotations

from typing import Literal

from disputedesk.db.models import Decision
from disputedesk.graph.state import MAX_REVISIONS, DisputeState, Verdict

AfterIntake = Literal["router", "escalate"]
AfterResolution = Literal["reviewer", "escalate"]
AfterReview = Literal["resolution", "output_check", "escalate"]
AfterOutputCheck = Literal["request_approval", "finalize", "escalate"]
AfterHuman = Literal["finalize", "escalate"]


def after_intake(state: DisputeState) -> AfterIntake:
    """A narrative the input guardrail blocked goes straight to a human, unread by any agent."""
    return "escalate" if state.input_flag is not None else "router"


def after_resolution(state: DisputeState) -> AfterResolution:
    """A draft that itself says "escalate" skips review and goes straight to escalation."""
    if state.draft is not None and state.draft.decision == Decision.ESCALATE:
        return "escalate"
    return "reviewer"


def after_review(state: DisputeState) -> AfterReview:
    """Revise (at most MAX_REVISIONS times), or approve and go on to the output check.

    The reviewer node bumps `revision_count` each time it asks for a revision, so with a
    limit of 2 the third "revise" is the one that escalates.
    """
    review = state.review
    if review is None:
        raise ValueError("reviewer produced no verdict")
    if review.verdict == Verdict.REVISE:
        return "resolution" if state.revision_count <= MAX_REVISIONS else "escalate"
    return "output_check"  # an approved letter is checked before anyone can act on it


def after_output_check(state: DisputeState) -> AfterOutputCheck:
    """A blocked letter goes to a human. Otherwise: a person approves high-risk cases first."""
    if state.output_flag is not None:
        return "escalate"
    if state.route is not None and state.route.approval_required:
        return "request_approval"
    return "finalize"


def after_human(state: DisputeState) -> AfterHuman:
    if state.human is None:
        raise ValueError("human gate resumed without a decision")
    return "finalize" if state.human.approved else "escalate"
