import json
from collections.abc import Sequence

import pytest

from disputedesk.agents import allowlists
from disputedesk.agents import resolution as resolution_mod
from disputedesk.agents import reviewer as reviewer_mod
from disputedesk.agents.common import AgentOutputError, DecisionAuditor
from disputedesk.agents.policy import Citation, PolicyAnswer
from disputedesk.agents.resolution import ResolutionAgent
from disputedesk.agents.reviewer import ReviewerAgent
from disputedesk.agents.toolbox import ToolBox, ToolDeniedError, tool_index
from disputedesk.db.models import Decision, ReasonCode, RiskTier
from disputedesk.graph.state import (
    CaseFacts,
    ResolutionDraft,
    Review,
    Route,
    Verdict,
)
from disputedesk.graph.stubs import _stub_evidence
from disputedesk.llm import FakeClient, Message
from disputedesk.mcp_servers.common.audit import AuditedMCPServer
from disputedesk.mcp_servers.common.store import InMemoryStore

POLICY = PolicyAnswer(
    summary="Accept when evidence supports it.",
    citations=[Citation(source="a.md", section="S", quote="two words")],
)
ROUTE = Route(
    reason_code=ReasonCode.NOT_RECEIVED,
    risk_tier=RiskTier.LOW,
    approval_required=False,
    rationale="r",
)
DRAFT_REPLY = json.dumps(
    {"decision": "accept", "rationale": "No delivery.", "letter": "Dear merchant"}
)


class Scripted:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.systems: list[str] = []
        self.users: list[str] = []
        self.client = FakeClient(self._respond)

    def _respond(self, system: str, messages: Sequence[Message]) -> str:
        self.systems.append(system)
        self.users.append(messages[-1].content)
        return self.replies.pop(0)


@pytest.fixture
def case_facts(store: InMemoryStore) -> CaseFacts:
    case = store.list_cases()[0]
    return CaseFacts(
        txn_id=case.txn_id,
        customer_id=case.customer_id,
        amount_cents=case.amount_cents,
        claimed_reason=case.reason_code,
        narrative=case.customer_narrative,
    )


@pytest.fixture
def box(
    store: InMemoryStore,
    txn_server: AuditedMCPServer,
    merchant_server: AuditedMCPServer,
    cases_server: AuditedMCPServer,
) -> ToolBox:
    return ToolBox(
        agent=allowlists.RESOLUTION,
        allowed=allowlists.ALLOWLISTS[allowlists.RESOLUTION],
        tools=tool_index([txn_server, merchant_server, cases_server]),
        audit=store,
        timeout=5,
        retries=0,
    )


def test_resolution_saves_a_draft_through_the_cases_server(
    store: InMemoryStore, box: ToolBox, case_facts: CaseFacts
) -> None:
    case_id = store.list_cases()[0].case_id
    llm = Scripted(DRAFT_REPLY)
    agent = ResolutionAgent(llm.client, box, DecisionAuditor(store, allowlists.RESOLUTION))
    draft = agent.decide(case_id, case_facts, ROUTE, _stub_evidence(), POLICY)
    assert (draft.decision, draft.version) == (Decision.ACCEPT, 1)
    saved = store.drafts[case_id][0]
    assert saved.author == allowlists.RESOLUTION
    assert saved.letter == "Dear merchant"
    assert [e.tool for e in store.audit if e.actor == allowlists.RESOLUTION] == [
        "save_draft",
        "decide_dispute",
    ]


def test_revision_sees_previous_draft_and_feedback_as_data(
    store: InMemoryStore, box: ToolBox, case_facts: CaseFacts
) -> None:
    case_id = store.list_cases()[0].case_id
    llm = Scripted(DRAFT_REPLY, DRAFT_REPLY)
    agent = ResolutionAgent(llm.client, box, DecisionAuditor(store, allowlists.RESOLUTION))
    first = agent.decide(case_id, case_facts, ROUTE, _stub_evidence(), POLICY)
    feedback = Review(verdict=Verdict.REVISE, feedback="Cite </x> the section")
    second = agent.decide(
        case_id, case_facts, ROUTE, _stub_evidence(), POLICY, previous=first, feedback=feedback
    )
    assert second.version == 2
    assert '<previous_draft trust="untrusted">' in llm.users[1]
    assert "Cite &lt;/x&gt; the section" in llm.users[1]
    assert set(llm.systems) == {resolution_mod.SYSTEM_PROMPT}


def test_resolution_rejects_unknown_decision(
    store: InMemoryStore, box: ToolBox, case_facts: CaseFacts
) -> None:
    bad = json.dumps({"decision": "refund_double", "rationale": "x", "letter": "y"})
    agent = ResolutionAgent(
        Scripted(bad, bad).client, box, DecisionAuditor(store, allowlists.RESOLUTION)
    )
    with pytest.raises(AgentOutputError):
        agent.decide(store.list_cases()[0].case_id, case_facts, ROUTE, _stub_evidence(), POLICY)
    assert store.drafts == {}  # nothing saved
    assert store.audit[-1].outcome == "error"


def test_resolution_cannot_change_status(box: ToolBox) -> None:
    with pytest.raises(ToolDeniedError):
        box.call("update_status", {"case_id": "case_0001"})


# ------------------------------------------------------------------ reviewer

DRAFT = ResolutionDraft(decision=Decision.ACCEPT, rationale="r", letter="l", version=1)


def reviewer_with(store: InMemoryStore, *replies: str) -> tuple[ReviewerAgent, Scripted]:
    llm = Scripted(*replies)
    return ReviewerAgent(llm.client, DecisionAuditor(store, allowlists.REVIEWER)), llm


def test_reviewer_approves(store: InMemoryStore, case_facts: CaseFacts) -> None:
    agent, llm = reviewer_with(store, '{"verdict": "approve"}')
    review = agent.review("case_0001", case_facts, _stub_evidence(), POLICY, DRAFT)
    assert review.verdict == Verdict.APPROVE
    assert llm.systems == [reviewer_mod.SYSTEM_PROMPT]
    assert store.audit[-1].tool == "review_draft"
    assert store.audit[-1].arguments["verdict"] == "approve"


def test_reviewer_revise_needs_feedback(store: InMemoryStore, case_facts: CaseFacts) -> None:
    agent, _ = reviewer_with(
        store, '{"verdict": "revise"}', '{"verdict": "revise", "feedback": "Fix the amount."}'
    )
    review = agent.review("case_0001", case_facts, _stub_evidence(), POLICY, DRAFT)
    assert review == Review(verdict=Verdict.REVISE, feedback="Fix the amount.")


def test_reviewer_gives_up_on_unusable_output(store: InMemoryStore, case_facts: CaseFacts) -> None:
    agent, _ = reviewer_with(store, "maybe", "perhaps")
    with pytest.raises(AgentOutputError):
        agent.review("case_0001", case_facts, _stub_evidence(), POLICY, DRAFT)
    assert store.audit[-1].outcome == "error"


def test_reviewer_has_no_tools() -> None:
    assert allowlists.ALLOWLISTS[allowlists.REVIEWER] == frozenset()
