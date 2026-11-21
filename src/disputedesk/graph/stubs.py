"""Stub nodes: canned outputs, no LLM and no tools. They let the graph skeleton be tested on
its own, and real nodes replace them one at a time."""

from __future__ import annotations

from datetime import UTC, datetime

from disputedesk.agents.policy import Citation, PolicyAnswer
from disputedesk.db.models import CaseStatus, Decision, ReasonCode, RiskTier
from disputedesk.db.seed import generate
from disputedesk.graph.builder import Nodes, Update
from disputedesk.graph.state import (
    DisputeState,
    Evidence,
    Leaning,
    ResolutionDraft,
    Review,
    Route,
    Verdict,
)
from disputedesk.mcp_servers.merchant.server import DisputeRate
from disputedesk.mcp_servers.transactions.server import CustomerHistory


def _stub_evidence() -> Evidence:
    """Real-looking evidence built from the first synthetic case (no tools involved)."""
    data = generate()
    txn = data.transactions[0]
    merchant = next(m for m in data.merchants if m.merchant_id == txn.merchant_id)
    now = datetime(2026, 11, 1, tzinfo=UTC)
    return Evidence(
        transaction=txn,
        merchant=merchant,
        customer_history=CustomerHistory(
            customer_id=txn.customer_id,
            window_start=now,
            window_end=now,
            txn_count=1,
            total_spend_cents=txn.amount_cents,
            refunded_count=0,
            distinct_merchants=1,
            top_merchants=[],
            dispute_count=1,
            first_txn_at=None,
            last_txn_at=None,
        ),
        merchant_dispute_rate=DisputeRate(
            merchant_id=merchant.merchant_id,
            window_start=now,
            window_end=now,
            txn_count=1,
            dispute_count=0,
            dispute_rate=0.0,
            by_reason={},
        ),
        summary="stub evidence",
        leaning=Leaning.INCONCLUSIVE,
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
        return {"evidence": _stub_evidence()}

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

    def output_check(state: DisputeState) -> Update:
        return {}

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
        output_check=output_check,
        request_approval=request_approval,
        finalize=finalize,
        escalate=escalate,
    )
