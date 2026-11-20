"""Route protection: every route needs a valid token, and the role must grant the scope."""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from disputedesk.api.app import create_app
from disputedesk.api.runtime import build_runtime
from disputedesk.config import Settings
from disputedesk.db.models import CaseStatus
from disputedesk.mcp_servers.common.store import InMemoryStore
from disputedesk.security.scopes import Role
from tests.conftest import make_settings
from tests.graph_helpers import (
    TEST_JWT_SECRET,
    StubRetriever,
    auth_headers,
    fake_llm,
    needs_approval,
)

type Send = Callable[[TestClient], int]

ROUTES: dict[str, Send] = {
    "start": lambda c: c.post("/disputes", json={"case_id": "case_0001"}).status_code,
    "read": lambda c: c.get("/disputes/case_0001").status_code,
    "approve": lambda c: c.post("/disputes/case_0001/approve", json={"approved": True}).status_code,
}


@pytest.fixture
def settings() -> Settings:
    return make_settings(jwt_secret=TEST_JWT_SECRET)


@pytest.fixture
def client(store: InMemoryStore, settings: Settings) -> TestClient:
    llm, _ = fake_llm()
    runtime = build_runtime(
        settings=settings,
        llm=llm,
        retriever=StubRetriever(),
        store=store,
        checkpointer=InMemorySaver(),
    )
    return TestClient(create_app(runtime, settings))


@pytest.mark.parametrize("route", ROUTES)
def test_no_token_is_401(client: TestClient, route: str) -> None:
    assert ROUTES[route](client) == 401


@pytest.mark.parametrize("route", ROUTES)
def test_garbage_and_wrong_scheme_tokens_are_401(client: TestClient, route: str) -> None:
    for header in ("Bearer not-a-jwt", "Basic dXNlcjpwYXNz", "Bearer"):
        client.headers["Authorization"] = header
        assert ROUTES[route](client) == 401


def test_token_signed_with_another_secret_is_401(client: TestClient) -> None:
    forged = make_settings(jwt_secret="forged-" + "y" * 40)
    client.headers.update(auth_headers(forged, role=Role.ADMIN))
    assert ROUTES["read"](client) == 401


def test_analyst_can_start_and_read_but_not_approve(
    client: TestClient, store: InMemoryStore, settings: Settings
) -> None:
    client.headers.update(auth_headers(settings, role=Role.ANALYST))
    case_id = next(
        c.case_id
        for c in store.list_cases()
        if c.status == CaseStatus.OPEN and needs_approval(c, store, settings)
    )
    assert client.post("/disputes", json={"case_id": case_id}).status_code == 202
    assert client.get(f"/disputes/{case_id}").status_code == 200
    resp = client.post(f"/disputes/{case_id}/approve", json={"approved": True})
    assert resp.status_code == 403
    # still waiting: the refused request changed nothing
    assert client.get(f"/disputes/{case_id}").json()["workflow"] == "awaiting_approval"


def test_approver_decision_is_recorded_under_the_token_subject(
    client: TestClient, store: InMemoryStore, settings: Settings
) -> None:
    case_id = next(
        c.case_id
        for c in store.list_cases()
        if c.status == CaseStatus.OPEN and needs_approval(c, store, settings)
    )
    client.headers.update(auth_headers(settings, sub="alice", role=Role.APPROVER))
    client.post("/disputes", json={"case_id": case_id})
    assert client.post(f"/disputes/{case_id}/approve", json={"approved": True}).status_code == 202
    reasons = [str(e.arguments.get("reason")) for e in store.audit if e.tool == "update_status"]
    assert any("alice" in r for r in reasons)


def test_app_refuses_to_start_without_a_jwt_secret(store: InMemoryStore) -> None:
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        create_app(settings=make_settings())
