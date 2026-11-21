"""M10: the letter passes the output guardrail before it can leave the system.

These run the whole graph on the fake model with the REAL guardrail (Presidio and NeMo rails), so
what is tested is the wiring and the rails together, not a stand-in.
"""

import json
from collections.abc import Sequence

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from disputedesk.agents import allowlists
from disputedesk.agents.fake_llm import agent_of, demo_responder
from disputedesk.api.runtime import Runtime, build_runtime
from disputedesk.db.models import AuditOutcome, CaseStatus, Decision
from disputedesk.db.seed import generate
from disputedesk.graph.state import DisputeState
from disputedesk.guardrails import interface
from disputedesk.llm import FakeClient, Message
from disputedesk.mcp_servers.common.store import InMemoryStore
from tests.conftest import make_settings
from tests.graph_helpers import TEST_JWT_SECRET, StubRetriever, needs_approval

CARD = "4111 1111 1111 1111"  # standard synthetic test card number
INVENTED = "$9,876.54"  # a dollar figure that appears nowhere in the case


@pytest.fixture(autouse=True)
def input_guard_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the output side is under test; keep the input rail from interfering."""
    from disputedesk.graph import system_nodes
    from disputedesk.guardrails.interface import Action, Verdict

    monkeypatch.setattr(
        system_nodes, "check_input", lambda text, **_: Verdict(action=Action.ALLOW, text=text)
    )


def run(
    case_id: str, store: InMemoryStore, *, letter_extra: str = ""
) -> tuple[Runtime, InMemoryStore]:
    """Run one case. `letter_extra` is appended to the resolution agent's letter."""

    def model(system: str, messages: Sequence[Message]) -> str:
        reply = demo_responder(system, messages)
        if letter_extra and agent_of(system) == "resolution":
            data = json.loads(reply)
            data["letter"] = f"{data['letter']} {letter_extra}"
            return json.dumps(data)
        return reply

    runtime = build_runtime(
        settings=make_settings(jwt_secret=TEST_JWT_SECRET),
        llm=FakeClient(model),
        retriever=StubRetriever(),
        store=store,
        checkpointer=InMemorySaver(),
    )
    runtime.claim_start(case_id)
    runtime.run(case_id)
    return runtime, store


def pick_case(*, approval: bool) -> str:
    """An open case whose clean run ends in an accept or reject (so there is a letter)."""
    probe = InMemoryStore(generate())
    settings = make_settings(jwt_secret=TEST_JWT_SECRET)
    for case in probe.list_cases():
        if case.status != CaseStatus.OPEN or needs_approval(case, probe, settings) != approval:
            continue
        runtime, _ = run(case.case_id, InMemoryStore(generate()))
        view = runtime.view(case.case_id)
        if view.draft is not None and view.draft.decision != Decision.ESCALATE:
            return case.case_id
    raise AssertionError("no suitable case in the seed data")


def final_state(runtime: Runtime, case_id: str) -> DisputeState:
    return DisputeState.model_validate(
        runtime.graph.get_state({"configurable": {"thread_id": case_id}}).values
    )


def test_a_clean_letter_passes_through_unchanged() -> None:
    case_id = pick_case(approval=False)
    runtime, store = run(case_id, InMemoryStore(generate()))
    state = final_state(runtime, case_id)
    assert state.path[-2:] == ["output_check", "finalize"]
    assert state.output_flag is None
    assert [d.author for d in store.drafts[case_id]] == ["resolution_agent"]  # no extra version


def test_pii_in_the_letter_is_redacted_and_saved_as_a_new_version() -> None:
    case_id = pick_case(approval=False)
    runtime, store = run(case_id, InMemoryStore(generate()), letter_extra=f"Card {CARD}.")

    drafts = store.drafts[case_id]
    assert [d.author for d in drafts] == ["resolution_agent", allowlists.OUTPUT_GUARD]
    assert CARD in drafts[0].letter  # the original version is kept as it was written
    assert CARD not in drafts[1].letter  # the version that goes out is not
    state = final_state(runtime, case_id)
    assert state.draft is not None and state.draft.version == 2
    assert CARD not in state.draft.letter
    assert state.final_status in (CaseStatus.RESOLVED_ACCEPTED, CaseStatus.RESOLVED_REJECTED)
    # The redaction is on the record under the guard's own name, and the audit holds no card.
    saves = [
        e for e in store.audit if e.tool == "save_draft" and e.actor == allowlists.OUTPUT_GUARD
    ]
    assert len(saves) == 1 and saves[0].outcome == AuditOutcome.OK


def test_an_ungrounded_figure_blocks_the_letter_and_escalates() -> None:
    case_id = pick_case(approval=False)
    runtime, store = run(
        case_id, InMemoryStore(generate()), letter_extra=f"We refund {INVENTED} in full."
    )
    state = final_state(runtime, case_id)
    assert state.final_status == CaseStatus.ESCALATED
    assert state.output_flag == "check grounding"
    assert state.path[-2:] == ["output_check", "escalate"]
    assert store.cases[case_id].status == CaseStatus.ESCALATED
    # The block is audited by rail name, and the figure is not in any audit row.
    blocks = [e for e in store.audit if e.server == "guardrails" and e.tool == "check_output"]
    assert blocks and blocks[-1].outcome == AuditOutcome.INVALID_INPUT
    assert all(INVENTED not in e.model_dump_json() for e in store.audit if e.server == "guardrails")
    reason = next(
        e
        for e in reversed(store.audit)
        if e.tool == "update_status" and "escalated" in str(e.arguments)
    )
    assert "output guardrail blocked the letter (check grounding)" in str(reason.arguments)


def test_a_blocked_letter_never_reaches_the_human_gate() -> None:
    case_id = pick_case(approval=True)
    runtime, _ = run(
        case_id, InMemoryStore(generate()), letter_extra=f"We refund {INVENTED} in full."
    )
    state = final_state(runtime, case_id)
    assert "request_approval" not in state.path and "human_gate" not in state.path
    assert state.final_status == CaseStatus.ESCALATED


def test_a_failing_guardrail_blocks_instead_of_letting_the_letter_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Broken:
        def check_output(self, text: str, grounding: object = None) -> None:
            raise RuntimeError("boom")

    monkeypatch.setattr(interface, "_edge", lambda: Broken())
    case_id = pick_case(approval=False)
    runtime, _ = run(case_id, InMemoryStore(generate()))
    state = final_state(runtime, case_id)
    assert state.output_flag == "nemo_error"
    assert state.final_status == CaseStatus.ESCALATED


def test_an_escalate_draft_sends_no_letter_so_it_is_not_checked() -> None:
    """The letter below would be blocked (an invented figure), but an escalate draft goes to a
    human with no letter, so the output step is skipped and nothing is flagged."""
    case_id = pick_case(approval=False)

    def model(system: str, messages: Sequence[Message]) -> str:
        if agent_of(system) == "resolution":
            return json.dumps(
                {
                    "decision": "escalate",
                    "confidence": 0.9,
                    "rationale": "The evidence conflicts.",
                    "letter": f"Refund {INVENTED}",
                }
            )
        return demo_responder(system, messages)

    store = InMemoryStore(generate())
    runtime = build_runtime(
        settings=make_settings(jwt_secret=TEST_JWT_SECRET),
        llm=FakeClient(model),
        retriever=StubRetriever(),
        store=store,
        checkpointer=InMemorySaver(),
    )
    runtime.claim_start(case_id)
    runtime.run(case_id)
    state = final_state(runtime, case_id)
    assert "output_check" not in state.path
    assert state.output_flag is None
    assert state.final_status == CaseStatus.ESCALATED


def test_the_output_guard_may_only_save_drafts() -> None:
    assert allowlists.ALLOWLISTS[allowlists.OUTPUT_GUARD] == frozenset({"save_draft"})
