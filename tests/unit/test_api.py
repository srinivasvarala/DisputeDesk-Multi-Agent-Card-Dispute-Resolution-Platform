"""API on in-memory parts: InMemoryStore, InMemorySaver, the fake LLM. No database needed."""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from disputedesk.api.app import create_app
from disputedesk.api.runtime import Runtime, build_runtime
from disputedesk.config import Settings
from disputedesk.db.models import CaseStatus
from disputedesk.llm import FakeClient
from disputedesk.mcp_servers.common.store import InMemoryStore
from tests.conftest import make_settings
from tests.graph_helpers import StubRetriever, always_revise, fake_llm, needs_approval

type Make = Callable[..., tuple[TestClient, Runtime, list[str]]]


@pytest.fixture
def settings() -> Settings:
    return make_settings()


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
        return TestClient(create_app(runtime)), runtime, calls

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

    resp = client.post(f"/disputes/{case_id}/approve", json={"approved": True, "approver": "ana"})
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
        json={"approved": False, "approver": "ana", "note": "amount looks wrong"},
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
    body = {"approved": True, "approver": "a"}
    assert client.post("/disputes/case_9999/approve", json=body).status_code == 404


def test_bad_request_bodies_are_422(make: Make) -> None:
    client, _, _ = make()
    assert client.post("/disputes", json={"case_id": "nope"}).status_code == 422
    assert client.post("/disputes", json={"case_id": "case_0001", "x": 1}).status_code == 422
    assert client.post("/disputes/case_0001/approve", json={"approved": True}).status_code == 422


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
    body = {"approved": True, "approver": "ana"}
    assert client.post(f"/disputes/{case_id}/approve", json=body).status_code == 409  # not started
    client.post("/disputes", json={"case_id": case_id})
    assert client.post(f"/disputes/{case_id}/approve", json=body).status_code == 409  # finished


def test_second_approval_is_a_conflict(
    make: Make, store: InMemoryStore, settings: Settings
) -> None:
    client, _, _ = make()
    case_id = open_case(store, settings, approval=True)
    client.post("/disputes", json={"case_id": case_id})
    body = {"approved": True, "approver": "ana"}
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
    client = TestClient(create_app(runtime))
    case_id = open_case(store, settings, approval=False)
    client.post("/disputes", json={"case_id": case_id})
    body = client.get(f"/disputes/{case_id}").json()
    assert body["workflow"] == "failed"
    assert body["case_status"] == "escalated"
    failures = [e for e in store.audit if e.tool == "workflow_failed"]
    assert len(failures) == 1
    assert failures[0].case_id == case_id
