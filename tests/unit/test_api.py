"""API on in-memory parts: InMemoryStore, InMemorySaver, the fake LLM. No database needed."""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from disputedesk.api.runtime import Runtime, build_runtime
from disputedesk.config import Settings
from disputedesk.db.models import CaseStatus
from disputedesk.graph.state import DisputeState
from disputedesk.llm import FakeClient
from disputedesk.mcp_servers.common.store import InMemoryStore
from tests.conftest import make_settings
from tests.graph_helpers import (
    TEST_JWT_SECRET,
    StubRetriever,
    always_revise,
    authed_client,
    fake_llm,
    needs_approval,
)

type Make = Callable[..., tuple[TestClient, Runtime, list[str]]]


@pytest.fixture
def settings() -> Settings:
    return make_settings(jwt_secret=TEST_JWT_SECRET)


@pytest.fixture
def make(store: InMemoryStore, settings: Settings) -> Make:
    def build(**llm_args: object) -> tuple[TestClient, Runtime, list[str]]:
        llm, calls = fake_llm(**llm_args)  # type: ignore[arg-type]
        runtime = build_runtime(
            settings=settings,
            llm=llm,
            retriever=StubRetriever(),
            store=store,
            checkpointer=InMemorySaver(),
        )
        return authed_client(runtime, settings), runtime, calls

    return build


def open_case(store: InMemoryStore, settings: Settings, *, approval: bool) -> str:
    return next(
        c.case_id
        for c in store.list_cases()
        if c.status == CaseStatus.OPEN and needs_approval(c, store, settings) == approval
    )


def test_low_risk_case_runs_to_resolution(
    make: Make, store: InMemoryStore, settings: Settings
) -> None:
    client, _, calls = make()
    case_id = open_case(store, settings, approval=False)

    assert client.post("/disputes", json={"case_id": case_id}).status_code == 202
    body = client.get(f"/disputes/{case_id}").json()

    assert body["workflow"] == "completed"
    assert body["case_status"] in ("resolved_accepted", "resolved_rejected")
    assert body["path"][-1] == "finalize"
    assert body["draft"]["version"] == 1
    assert calls == ["router", "evidence", "policy", "resolution", "reviewer"]


def test_high_risk_case_pauses_then_resumes(
    make: Make, store: InMemoryStore, settings: Settings
) -> None:
    client, _, _ = make()
    case_id = open_case(store, settings, approval=True)
    client.post("/disputes", json={"case_id": case_id})

    paused = client.get(f"/disputes/{case_id}").json()
    assert paused["workflow"] == "awaiting_approval"
    assert paused["case_status"] == "pending_approval"
    assert paused["path"][-1] == "request_approval"

    resp = client.post(f"/disputes/{case_id}/approve", json={"approved": True})
    assert resp.status_code == 202
    done = client.get(f"/disputes/{case_id}").json()
    assert done["workflow"] == "completed"
    assert done["case_status"].startswith("resolved_")
    assert done["path"][-3:] == ["request_approval", "human_gate", "finalize"]


def test_human_rejection_escalates(make: Make, store: InMemoryStore, settings: Settings) -> None:
    client, _, _ = make()
    case_id = open_case(store, settings, approval=True)
    client.post("/disputes", json={"case_id": case_id})
    client.post(
        f"/disputes/{case_id}/approve",
        json={"approved": False, "note": "amount looks wrong"},
    )
    body = client.get(f"/disputes/{case_id}").json()
    assert body["case_status"] == "escalated"
    assert body["workflow"] == "completed"


def test_reviewer_never_satisfied_escalates_after_two_revisions(
    make: Make, store: InMemoryStore, settings: Settings
) -> None:
    client, _, calls = make(reviews=always_revise())
    case_id = open_case(store, settings, approval=False)
    client.post("/disputes", json={"case_id": case_id})
    body = client.get(f"/disputes/{case_id}").json()
    assert body["case_status"] == "escalated"
    assert body["revision_count"] == 3
    assert body["draft"]["version"] == 3
    assert calls.count("resolution") == 3
    assert calls.count("reviewer") == 3


def test_unknown_case_is_404(make: Make) -> None:
    client, _, _ = make()
    assert client.post("/disputes", json={"case_id": "case_9999"}).status_code == 404
    assert client.get("/disputes/case_9999").status_code == 404
    body = {"approved": True}
    assert client.post("/disputes/case_9999/approve", json=body).status_code == 404


def test_bad_request_bodies_are_422(make: Make) -> None:
    client, _, _ = make()
    assert client.post("/disputes", json={"case_id": "nope"}).status_code == 422
    assert client.post("/disputes", json={"case_id": "case_0001", "x": 1}).status_code == 422
    assert client.post("/disputes/case_0001/approve", json={}).status_code == 422
    # the approver is the token's subject: a body that names one is refused
    body = {"approved": True, "approver": "someone-else"}
    assert client.post("/disputes/case_0001/approve", json=body).status_code == 422


def test_starting_twice_is_a_conflict(make: Make, store: InMemoryStore, settings: Settings) -> None:
    client, _, _ = make()
    case_id = open_case(store, settings, approval=False)
    assert client.post("/disputes", json={"case_id": case_id}).status_code == 202
    assert client.post("/disputes", json={"case_id": case_id}).status_code == 409


def test_approving_a_case_that_is_not_waiting_is_a_conflict(
    make: Make, store: InMemoryStore, settings: Settings
) -> None:
    client, _, _ = make()
    case_id = open_case(store, settings, approval=False)
    body = {"approved": True}
    assert client.post(f"/disputes/{case_id}/approve", json=body).status_code == 409  # not started
    client.post("/disputes", json={"case_id": case_id})
    assert client.post(f"/disputes/{case_id}/approve", json=body).status_code == 409  # finished


def test_second_approval_is_a_conflict(
    make: Make, store: InMemoryStore, settings: Settings
) -> None:
    client, _, _ = make()
    case_id = open_case(store, settings, approval=True)
    client.post("/disputes", json={"case_id": case_id})
    body = {"approved": True}
    assert client.post(f"/disputes/{case_id}/approve", json=body).status_code == 202
    assert client.post(f"/disputes/{case_id}/approve", json=body).status_code == 409


def test_a_crashed_run_escalates_the_case(store: InMemoryStore, settings: Settings) -> None:
    broken = FakeClient(lambda system, messages: "not json at all")
    runtime = build_runtime(
        settings=settings,
        llm=broken,
        retriever=StubRetriever(),
        store=store,
        checkpointer=InMemorySaver(),
    )
    client = authed_client(runtime, settings)
    case_id = open_case(store, settings, approval=False)
    client.post("/disputes", json={"case_id": case_id})
    body = client.get(f"/disputes/{case_id}").json()
    assert body["workflow"] == "failed"
    assert body["case_status"] == "escalated"
    failures = [e for e in store.audit if e.tool == "workflow_failed"]
    assert len(failures) == 1
    assert failures[0].case_id == case_id


def test_failing_an_already_escalated_case_does_not_error(
    store: InMemoryStore, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    """M7 baseline: a crash after the case was escalated made `_fail` try escalated -> escalated,
    which the state machine rejects, and logged a second traceback."""
    llm, _ = fake_llm()
    runtime = build_runtime(
        settings=settings,
        llm=llm,
        retriever=StubRetriever(),
        store=store,
        checkpointer=InMemorySaver(),
    )
    case_id = open_case(store, settings, approval=False)
    runtime.failure_tools.call(
        "update_status", {"case_id": case_id, "new_status": "in_review", "reason": "test"}
    )
    runtime.failure_tools.call(
        "update_status", {"case_id": case_id, "new_status": "escalated", "reason": "test"}
    )
    with caplog.at_level("ERROR"):
        runtime._fail(case_id, RuntimeError("late crash"))
    assert "could not escalate" not in caplog.text
    assert store.get_case(case_id).status == CaseStatus.ESCALATED  # type: ignore[union-attr]
    assert len([e for e in store.audit if e.tool == "workflow_failed"]) == 1


class _Snapshot:
    def __init__(self, values: dict[str, object], next_: tuple[str, ...]) -> None:
        self.values, self.next, self.tasks = values, next_, []


class _FakeGraph:
    def __init__(self, snapshot: _Snapshot) -> None:
        self._snapshot = snapshot

    def get_state(self, config: object) -> _Snapshot:
        return self._snapshot


def test_empty_next_between_nodes_is_not_reported_as_completed(store: InMemoryStore) -> None:
    """A checkpoint taken between two nodes can have no `next` yet; the case is still running."""
    case_id = store.list_cases()[0].case_id
    mid_run = _Snapshot(DisputeState(case_id=case_id, path=["intake", "router"]).model_dump(), ())
    runtime = Runtime(graph=_FakeGraph(mid_run), store=store, failure_tools=None)  # type: ignore[arg-type]
    assert runtime.view(case_id).workflow == "running"

    finished = DisputeState(case_id=case_id, final_status=CaseStatus.RESOLVED_ACCEPTED)
    runtime.graph = _FakeGraph(_Snapshot(finished.model_dump(), ()))  # type: ignore[assignment]
    assert runtime.view(case_id).workflow == "completed"


def test_a_run_that_crashes_while_resuming_is_failed_and_cannot_be_approved_again(
    store: InMemoryStore, settings: Settings
) -> None:
    """After the human approves, finalize crashes: the case is escalated, `GET` must say
    failed (not awaiting approval) and a second approval must be refused."""
    llm, _ = fake_llm()
    runtime = build_runtime(
        settings=settings,
        llm=llm,
        retriever=StubRetriever(),
        store=store,
        checkpointer=InMemorySaver(),
    )
    client = authed_client(runtime, settings)
    case_id = open_case(store, settings, approval=True)
    client.post("/disputes", json={"case_id": case_id})

    original = store.set_case_status

    def boom(case_id: str, **kwargs: object) -> object:
        if kwargs["new"] in (CaseStatus.RESOLVED_ACCEPTED, CaseStatus.RESOLVED_REJECTED):
            raise RuntimeError("db down")
        return original(case_id, **kwargs)  # type: ignore[arg-type]

    store.set_case_status = boom  # type: ignore[method-assign,assignment]
    body = {"approved": True}
    assert client.post(f"/disputes/{case_id}/approve", json=body).status_code == 202

    view = client.get(f"/disputes/{case_id}").json()
    assert view["workflow"] == "failed"
    assert view["case_status"] == "escalated"
    assert client.post(f"/disputes/{case_id}/approve", json=body).status_code == 409
    failure = [e for e in store.audit if e.tool == "workflow_failed"][-1]
    assert failure.error == "ToolCallError"  # type only, no message text
