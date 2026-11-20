"""Stub nodes: canned outputs, no LLM and no tools. They let the graph skeleton be tested on
its own, and real nodes replace them one at a time."""

from __future__ import annotations

from disputedesk.agents.policy import Citation, PolicyAnswer
from disputedesk.db.models import CaseStatus, Decision, ReasonCode, RiskTier
from disputedesk.graph.builder import Nodes, Update
from disputedesk.graph.state import (
    DisputeState,
    Evidence,
    ResolutionDraft,
    Review,
    Route,
    Verdict,
)


def stub_nodes(
    *,
    revisions_requested: int = 0,
    approval_required: bool = False,
    decision: Decision = Decision.ACCEPT,
) -> Nodes:
    """`revisions_requested`: how many times the stub reviewer says "revise" before approving
    (use a large number to force the escalation path)."""

    def intake(state: DisputeState) -> Update:
        return {}

    def router(state: DisputeState) -> Update:
        return {
            "route": Route(
                reason_code=ReasonCode.NOT_RECEIVED,
                risk_tier=RiskTier.HIGH if approval_required else RiskTier.LOW,
                approval_required=approval_required,
                rationale="stub",
            )
        }

    def evidence(state: DisputeState) -> Update:
        return {"evidence": Evidence(summary="stub evidence")}

    def policy(state: DisputeState) -> Update:
        cite = Citation(source="stub.md", section="Stub", quote="stub quote text")
        return {"policy": PolicyAnswer(summary="stub policy", citations=[cite])}

    def resolution(state: DisputeState) -> Update:
        version = state.draft.version + 1 if state.draft else 1
        return {
            "draft": ResolutionDraft(
                decision=decision, rationale="stub", letter="stub letter", version=version
            )
        }

    def reviewer(state: DisputeState) -> Update:
        if state.revision_count < revisions_requested:
            return {
                "review": Review(verdict=Verdict.REVISE, feedback="stub feedback"),
                "revision_count": state.revision_count + 1,
            }
        return {"review": Review(verdict=Verdict.APPROVE)}

    def request_approval(state: DisputeState) -> Update:
        return {}

    def finalize(state: DisputeState) -> Update:
        accepted = state.draft is not None and state.draft.decision == Decision.ACCEPT
        return {
            "final_status": CaseStatus.RESOLVED_ACCEPTED
            if accepted
            else CaseStatus.RESOLVED_REJECTED
        }

    def escalate(state: DisputeState) -> Update:
        return {"final_status": CaseStatus.ESCALATED}

    return Nodes(
        intake=intake,
        router=router,
        evidence=evidence,
        policy=policy,
        resolution=resolution,
        reviewer=reviewer,
        request_approval=request_approval,
        finalize=finalize,
        escalate=escalate,
    )
