"""Deterministic workflow steps (no LLM): read the case, and move it through its statuses.

They use the `cases` server's tools (allowlist: get_case, update_status), so every status change
is validated by the workflow state machine and audited by the server.
"""

from __future__ import annotations

from collections.abc import Callable

from disputedesk.agents import allowlists
from disputedesk.agents.toolbox import ToolBox
from disputedesk.db.models import CaseStatus, Decision
from disputedesk.graph.builder import Update
from disputedesk.graph.state import MAX_REVISIONS, CaseFacts, DisputeState
from disputedesk.guardrails.interface import Action, Verdict, check_input
from disputedesk.mcp_servers.common.store import AuditSink

InputCheck = Callable[..., Verdict]


class WorkflowSteps:
    def __init__(
        self,
        tools: ToolBox,
        audit: AuditSink | None = None,
        input_check: InputCheck | None = None,
    ) -> None:
        self._tools = tools
        self._audit = audit
        # None = the real guardrail. Evals pass an allow-all check to measure what the layers
        # below the input rail do when it misses; it is looked up at call time so tests can
        # also replace `check_input` on this module.
        self._input_check = input_check

    def intake(self, state: DisputeState) -> Update:
        """Load the case and move it open -> in_review (skipped if already past `open`)."""
        view = self._tools.call("get_case", {"case_id": state.case_id})
        case = view["case"]
        if case["status"] == CaseStatus.OPEN:
            self._set_status(state.case_id, CaseStatus.IN_REVIEW, "workflow started")
        # The narrative is customer-written, so it is checked before any agent reads it. A block
        # sends the case to a human; a redaction means the agents only ever see the clean text.
        verdict = (self._input_check or check_input)(
            case["customer_narrative"],
            agent=allowlists.SYSTEM,
            case_id=state.case_id,
            audit=self._audit,
        )
        narrative = verdict.text if verdict.action == Action.REDACT else case["customer_narrative"]
        facts = CaseFacts(
            txn_id=case["txn_id"],
            customer_id=case["customer_id"],
            amount_cents=case["amount_cents"],
            claimed_reason=case["reason_code"],
            narrative=narrative or "",
            filed_at=case.get("opened_at"),
            fraud_declaration_signed=case.get("fraud_declaration_signed"),
            card_reissued=case.get("card_reissued"),
            card_reported_lost_at=case.get("card_reported_lost_at"),
            merchant_contacted_at=case.get("merchant_contacted_at"),
            return_tracking_provided=case.get("return_tracking_provided"),
            merchant_refused_return=case.get("merchant_refused_return"),
        )
        if verdict.action == Action.BLOCK:
            return {"facts": facts, "input_flag": verdict.rail or "input guardrail"}
        return {"facts": facts}

    def request_approval(self, state: DisputeState) -> Update:
        self._set_status(state.case_id, CaseStatus.PENDING_APPROVAL, "waiting for human approval")
        return {}

    def finalize(self, state: DisputeState) -> Update:
        if state.draft is None:
            raise ValueError("cannot finalize without a draft")
        status = (
            CaseStatus.RESOLVED_ACCEPTED
            if state.draft.decision == Decision.ACCEPT
            else CaseStatus.RESOLVED_REJECTED
        )
        who = f"approved by {state.human.approver}" if state.human else "no approval needed"
        self._set_status(state.case_id, status, f"draft v{state.draft.version} final; {who}")
        return {"final_status": status}

    def escalate(self, state: DisputeState) -> Update:
        self._set_status(state.case_id, CaseStatus.ESCALATED, escalation_reason(state))
        return {"final_status": CaseStatus.ESCALATED}

    def _set_status(self, case_id: str, status: CaseStatus, reason: str) -> None:
        self._tools.call(
            "update_status", {"case_id": case_id, "new_status": status, "reason": reason}
        )


def escalation_reason(state: DisputeState) -> str:
    if state.input_flag is not None:
        return f"input guardrail flagged the narrative ({state.input_flag}); referred to a human"
    if state.output_flag is not None:
        return f"output guardrail blocked the letter ({state.output_flag}); referred to a human"
    if state.human is not None and not state.human.approved:
        return f"human reviewer {state.human.approver} did not approve: {state.human.note}"[:1000]
    if state.draft is not None and state.draft.decision == Decision.ESCALATE:
        return "resolution agent could not decide and escalated"
    if state.revision_count > MAX_REVISIONS:
        return f"reviewer still not satisfied after {MAX_REVISIONS} revisions"
    return "escalated by workflow"
