"""Graph skeleton on stub nodes: routing, the revision cap and the human-approval pause."""

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from disputedesk.db.models import CaseStatus, Decision
from disputedesk.graph.builder import build_graph
from disputedesk.graph.state import DisputeState
from disputedesk.graph.stubs import stub_nodes

BASE = ["intake", "router", "evidence", "policy", "resolution"]


def run(thread: str = "t1", **stub_args: Any) -> tuple[Any, dict[str, Any]]:
    graph = build_graph(stub_nodes(**stub_args), InMemorySaver())
    config: Any = {"configurable": {"thread_id": thread}}
    graph.invoke(DisputeState(case_id="case_0001"), config)
    return graph, config


def values(graph: Any, config: Any) -> DisputeState:
    return DisputeState.model_validate(graph.get_state(config).values)


def test_happy_path_low_risk_resolves_without_a_human() -> None:
    graph, config = run()
    state = values(graph, config)
    assert state.path == [*BASE, "reviewer", "finalize"]
    assert state.final_status == CaseStatus.RESOLVED_ACCEPTED
    assert graph.get_state(config).next == ()


def test_one_revision_then_approve() -> None:
    graph, config = run(revisions_requested=1)
    state = values(graph, config)
    assert state.path == [*BASE, "reviewer", "resolution", "reviewer", "finalize"]
    assert state.revision_count == 1
    assert state.draft is not None
    assert state.draft.version == 2


def test_two_revisions_are_allowed() -> None:
    graph, config = run(revisions_requested=2)
    state = values(graph, config)
    assert state.revision_count == 2
    assert state.final_status == CaseStatus.RESOLVED_ACCEPTED


def test_third_revision_request_escalates() -> None:
    graph, config = run(revisions_requested=99)
    state = values(graph, config)
    assert state.revision_count == 3
    assert state.draft is not None
    assert state.draft.version == 3  # the initial draft plus exactly two revisions
    assert state.path[-2:] == ["reviewer", "escalate"]
    assert state.final_status == CaseStatus.ESCALATED


def test_escalate_draft_skips_review() -> None:
    graph, config = run(decision=Decision.ESCALATE)
    state = values(graph, config)
    assert state.path == [*BASE, "escalate"]
    assert state.final_status == CaseStatus.ESCALATED


def test_high_risk_pauses_then_resumes_on_approval() -> None:
    graph, config = run(approval_required=True)
    snapshot = graph.get_state(config)
    assert snapshot.next == ("human_gate",)
    assert values(graph, config).final_status is None

    graph.invoke(Command(resume={"approved": True, "approver": "ana"}), config)
    state = values(graph, config)
    assert state.path[-3:] == ["request_approval", "human_gate", "finalize"]
    assert state.final_status == CaseStatus.RESOLVED_ACCEPTED


def test_human_rejection_escalates() -> None:
    graph, config = run(approval_required=True)
    graph.invoke(Command(resume={"approved": False, "approver": "ana"}), config)
    assert values(graph, config).final_status == CaseStatus.ESCALATED
