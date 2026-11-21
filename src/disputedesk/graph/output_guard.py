"""The output guardrail step: the letter is checked before it can leave the system.

Runs after the reviewer approves a draft and before the case is finalised or sent to a human
approver. It uses `guardrails/interface.py::check_output` (PII, then grounding of dollar amounts
and card digits). The outcomes are:

- clean: nothing changes.
- PII found: the letter is redacted and saved as a NEW draft version, written by this step's own
  actor so the audit log shows who changed it.
- blocked, or the guardrail itself failed: the case is escalated to a human. No retry and no
  silent pass (fail closed).

An `escalate` draft sends no letter, so it is not checked.
"""

from __future__ import annotations

from collections.abc import Callable

from disputedesk.agents import allowlists
from disputedesk.agents.toolbox import ToolBox
from disputedesk.db.models import CaseDraft, Decision
from disputedesk.graph.builder import Update
from disputedesk.graph.state import DisputeState
from disputedesk.guardrails.interface import Action, Verdict, check_output
from disputedesk.mcp_servers.common.store import AuditSink

OutputCheck = Callable[..., Verdict]


def _dollars(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def grounding_material(state: DisputeState) -> list[str]:
    """What a letter may quote figures from: the case's own amounts and the policy and evidence
    text. The letter itself and the analyst's draft are NOT included, so a figure the model
    invented cannot vouch for itself."""
    assert state.facts is not None and state.evidence is not None and state.policy is not None
    amounts = [state.facts.amount_cents, state.evidence.transaction.amount_cents]
    amounts += [t.amount_cents for t in state.evidence.possible_duplicates]
    material = [", ".join(_dollars(a) for a in amounts)]
    material.append(state.evidence.model_dump_json())
    material.append(state.policy.summary)
    material += [f"{c.source} {c.section} {c.quote}" for c in state.policy.citations]
    return material


class OutputGuard:
    def __init__(
        self,
        tools: ToolBox,
        audit: AuditSink | None = None,
        output_check: OutputCheck | None = None,
    ) -> None:
        self._tools = tools
        self._audit = audit
        # None = the real guardrail; looked up at call time so tests can replace `check_output`
        # on this module. Evals may pass an allow-all check to measure the layers below.
        self._output_check = output_check

    def check(self, state: DisputeState) -> Update:
        draft = state.draft
        if draft is None:
            raise ValueError("cannot check a letter without a draft")
        if draft.decision == Decision.ESCALATE:
            return {}  # no letter goes out
        verdict = (self._output_check or check_output)(
            draft.letter,
            agent=allowlists.OUTPUT_GUARD,
            case_id=state.case_id,
            grounding=grounding_material(state),
            audit=self._audit,
        )
        if not verdict.allowed:
            return {"output_flag": verdict.rail or "output guardrail"}
        if verdict.action == Action.REDACT and verdict.text and verdict.text != draft.letter:
            saved = CaseDraft.model_validate(
                self._tools.call(
                    "save_draft",
                    {
                        "case_id": state.case_id,
                        "decision": draft.decision,
                        "rationale": draft.rationale,
                        "letter": verdict.text,
                        "author": allowlists.OUTPUT_GUARD,
                    },
                )
            )
            return {
                "draft": draft.model_copy(update={"letter": saved.letter, "version": saved.version})
            }
        return {}
