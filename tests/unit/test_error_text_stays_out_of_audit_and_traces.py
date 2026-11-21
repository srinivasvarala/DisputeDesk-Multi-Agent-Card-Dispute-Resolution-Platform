"""M10: when an agent fails, no model or customer text reaches the audit log or a trace.

The audit log is append-only, so a leak cannot be undone. A schema error from pydantic echoes the
offending value, and the offending value here is the model's reply, which in a real incident can
repeat the cardholder's narrative. The check: make each agent's model reply carry a canary string
in a field that fails validation, run the case, and look for the canary everywhere it must not be.
"""

import json
from collections.abc import Sequence

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from disputedesk.agents.common import AgentOutputError, complete_json
from disputedesk.agents.fake_llm import agent_of, demo_responder
from disputedesk.api.runtime import build_runtime
from disputedesk.db.models import AuditOutcome, CaseStatus
from disputedesk.db.seed import generate
from disputedesk.graph.state import Route
from disputedesk.llm import FakeClient, Message
from disputedesk.mcp_servers.common.store import InMemoryStore
from tests.conftest import make_settings
from tests.graph_helpers import TEST_JWT_SECRET, StubRetriever
from tests.otel_helpers import install_test_sdk

CANARY = "CANARY-9f2c-narrative-text"
# Valid JSON, wrong shape: every field that has a schema fails and echoes its value.
BAD_REPLY = json.dumps(
    {
        "reason_code": CANARY,
        "leaning": CANARY,
        "decision": CANARY,
        "verdict": CANARY,
        "summary": {"x": CANARY},
        "citations": CANARY,
        "rationale": "x",
        "letter": "x",
    }
)
AGENTS = ["router", "evidence", "policy", "resolution", "reviewer"]


@pytest.fixture(autouse=True)
def input_guard_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    from disputedesk.graph import system_nodes
    from disputedesk.guardrails.interface import Action, Verdict

    monkeypatch.setattr(
        system_nodes, "check_input", lambda text, **_: Verdict(action=Action.ALLOW, text=text)
    )


def test_control_the_canary_really_is_in_the_exception_message() -> None:
    """Without this the tests below could pass only because the error text never had the canary."""
    llm = FakeClient(lambda s, m: BAD_REPLY)
    with pytest.raises(AgentOutputError) as caught:
        complete_json(llm, system="s", user="u", schema=Route)
    assert CANARY in str(caught.value)


@pytest.mark.parametrize("failing_agent", AGENTS)
def test_a_failing_agent_leaks_nothing_into_the_audit_log_or_traces(failing_agent: str) -> None:
    spans, _ = install_test_sdk()
    spans.clear()
    store = InMemoryStore(generate())
    case_id = next(c.case_id for c in store.list_cases() if c.status == CaseStatus.OPEN)

    def model(system: str, messages: Sequence[Message]) -> str:
        return BAD_REPLY if agent_of(system) == failing_agent else demo_responder(system, messages)

    runtime = build_runtime(
        settings=make_settings(jwt_secret=TEST_JWT_SECRET),
        llm=FakeClient(model),
        retriever=StubRetriever(),
        store=store,
        checkpointer=InMemorySaver(),
    )
    runtime.claim_start(case_id)
    runtime.run(case_id)

    # The run failed where we made it fail, and the case went to a human.
    assert store.cases[case_id].status == CaseStatus.ESCALATED
    failures = [e for e in store.audit if e.outcome == AuditOutcome.ERROR]
    assert failures, "the failure was not audited"
    assert all(CANARY not in e.model_dump_json() for e in store.audit)
    assert all(CANARY not in span.to_json() for span in spans.get_finished_spans())
    # What is recorded is the exception type.
    assert {e.error for e in failures if e.tool != "workflow_failed"} <= {
        "AgentOutputError",
        "PolicyAgentError",
    }
