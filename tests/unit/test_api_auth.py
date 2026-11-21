"""Route protection: every route needs a valid token, and the role must grant the scope."""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from disputedesk.api.app import create_app
from disputedesk.api.runtime import build_runtime
from disputedesk.config import Settings
from disputedesk.db.models import AuditEntry, AuditOutcome, CaseStatus
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
    "list": lambda c: c.get("/disputes").status_code,
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
    client.headers.update(auth_headers(settings, sub="maya", role=Role.ANALYST))
    client.post("/disputes", json={"case_id": case_id})
    client.headers.update(auth_headers(settings, sub="alice", role=Role.APPROVER))
    assert client.post(f"/disputes/{case_id}/approve", json={"approved": True}).status_code == 202
    reasons = [str(e.arguments.get("reason")) for e in store.audit if e.tool == "update_status"]
    assert any("alice" in r for r in reasons)


def test_app_refuses_to_start_without_a_jwt_secret(store: InMemoryStore) -> None:
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        create_app(settings=make_settings())


# --- audit ------------------------------------------------------------------------------


def test_wrong_role_is_audited_but_anonymous_401_is_only_logged(
    client: TestClient, store: InMemoryStore, settings: Settings
) -> None:
    """audit_log is append-only, so unauthenticated traffic must not be able to fill it."""
    before = len(store.audit)
    for _ in range(5):
        client.get("/disputes/case_0001")  # no token
    assert len(store.audit) == before

    client.headers.update(auth_headers(settings, sub="mallory", role=Role.ANALYST))
    client.post("/disputes/case_0001/approve", json={"approved": True})  # wrong role
    (denied,) = store.audit[before:]
    assert (denied.actor, denied.outcome) == ("user:mallory", AuditOutcome.DENIED)
    assert denied.tool == "POST /disputes/{case_id}/approve"
    assert denied.case_id == "case_0001"
    assert denied.arguments == {"scope": "disputes:approve", "status": 403, "role": "analyst"}


def test_a_user_named_like_an_agent_cannot_pass_for_it_in_the_log(
    client: TestClient, store: InMemoryStore, settings: Settings
) -> None:
    client.headers.update(auth_headers(settings, sub="resolution_agent", role=Role.ANALYST))
    client.post("/disputes", json={"case_id": "case_0001"})
    api_rows = [e for e in store.audit if e.server == "api"]
    assert [e.actor for e in api_rows] == ["user:resolution_agent"]


def test_a_broken_audit_log_does_not_turn_a_denial_into_a_success(
    client: TestClient, store: InMemoryStore
) -> None:
    def boom(entry: object) -> None:
        raise RuntimeError("audit down")

    store.write_audit = boom  # type: ignore[method-assign,assignment]
    assert client.get("/disputes/case_0001").status_code == 401


def test_start_and_approve_are_audited_under_the_caller(
    client: TestClient, store: InMemoryStore, settings: Settings
) -> None:
    case_id = next(
        c.case_id
        for c in store.list_cases()
        if c.status == CaseStatus.OPEN and needs_approval(c, store, settings)
    )
    client.headers.update(auth_headers(settings, sub="maya", role=Role.ANALYST))
    client.post("/disputes", json={"case_id": case_id})
    client.headers.update(auth_headers(settings, sub="alice", role=Role.APPROVER))
    client.post(f"/disputes/{case_id}/approve", json={"approved": False, "note": "amount is off"})

    api_rows = [e for e in store.audit if e.server == "api"]
    assert [(e.actor, e.tool, e.case_id) for e in api_rows] == [
        ("user:maya", "start_dispute", case_id),
        ("user:alice", "approve_dispute", case_id),
    ]
    # The approver's own words are on the record next to the decision.
    assert api_rows[1].arguments == {"role": "approver", "approved": False, "note": "amount is off"}


def test_audit_endpoint_is_admin_only_and_filters_by_case(
    client: TestClient, store: InMemoryStore, settings: Settings
) -> None:
    client.headers.update(auth_headers(settings, sub="alice", role=Role.APPROVER))
    client.post("/disputes", json={"case_id": "case_0001"})
    assert client.get("/audit").status_code == 403

    client.headers.update(auth_headers(settings, sub="root", role=Role.ADMIN))
    everything = client.get("/audit").json()
    assert everything and everything[0]["created_at"] >= everything[-1]["created_at"]
    one_case = client.get("/audit", params={"case_id": "case_0001"}).json()
    assert one_case and {r["case_id"] for r in one_case} == {"case_0001"}
    assert client.get("/audit", params={"limit": 0}).status_code == 422
    assert client.get("/audit", params={"case_id": "nope"}).status_code == 422


@pytest.mark.parametrize("route", ["start", "approve"])
def test_a_failed_audit_write_does_not_leave_the_case_claimed(
    store: InMemoryStore, settings: Settings, route: str
) -> None:
    """If the audit row cannot be written the request fails, and a retry must work (not 409)."""
    llm, _ = fake_llm()
    runtime = build_runtime(
        settings=settings,
        llm=llm,
        retriever=StubRetriever(),
        store=store,
        checkpointer=InMemorySaver(),
    )
    client = TestClient(create_app(runtime, settings), raise_server_exceptions=False)
    client.headers.update(auth_headers(settings, role=Role.APPROVER))
    case_id = next(
        c.case_id
        for c in store.list_cases()
        if c.status == CaseStatus.OPEN and needs_approval(c, store, settings)
    )
    if route == "approve":
        assert client.post("/disputes", json={"case_id": case_id}).status_code == 202
    original = store.write_audit
    broken = True

    def flaky(entry: AuditEntry) -> None:
        if broken and entry.server == "api":
            raise RuntimeError("audit down")
        original(entry)

    store.write_audit = flaky  # type: ignore[method-assign]
    path, body = (
        ("/disputes", {"case_id": case_id})
        if route == "start"
        else (f"/disputes/{case_id}/approve", {"approved": True})
    )
    # Approving as someone other than the user who started the case (maker-checker).
    who = auth_headers(settings, sub="bea", role=Role.APPROVER) if route == "approve" else None
    assert client.post(path, json=body, headers=who).status_code == 500

    broken = False
    # not 409 "already being processed"
    assert client.post(path, json=body, headers=who).status_code == 202


def test_reading_the_audit_log_is_itself_audited_and_fails_closed(
    client: TestClient, store: InMemoryStore, settings: Settings
) -> None:
    client.headers.update(auth_headers(settings, sub="root", role=Role.ADMIN))
    rows = client.get("/audit", params={"case_id": "case_0001", "limit": 5}).json()
    reads = [e for e in store.audit if e.tool == "read_audit"]
    assert [(e.actor, e.case_id, e.arguments) for e in reads] == [
        ("user:root", "case_0001", {"role": "admin", "limit": 5})
    ]
    assert rows[0]["tool"] == "read_audit"  # newest first: the read shows up in its own result

    def boom(entry: AuditEntry) -> None:
        raise RuntimeError("audit down")

    store.write_audit = boom  # type: ignore[method-assign]
    unsafe = TestClient(client.app, raise_server_exceptions=False)  # type: ignore[arg-type]
    unsafe.headers.update(client.headers)
    assert unsafe.get("/audit").status_code == 500  # nothing is disclosed unaudited


def test_case_reads_are_not_audited(
    client: TestClient, store: InMemoryStore, settings: Settings
) -> None:
    client.headers.update(auth_headers(settings, role=Role.ANALYST))
    before = len(store.audit)
    assert client.get("/disputes/case_0001").status_code == 200
    assert len(store.audit) == before


# --- maker-checker: the user who started a case cannot approve it ---


def _high_risk_case(store: InMemoryStore, settings: Settings) -> str:
    return next(
        c.case_id
        for c in store.list_cases()
        if c.status == CaseStatus.OPEN and needs_approval(c, store, settings)
    )


@pytest.mark.parametrize("role", [Role.APPROVER, Role.ADMIN])  # no exemption for admins
def test_the_user_who_started_a_case_cannot_approve_it(
    client: TestClient, store: InMemoryStore, settings: Settings, role: Role
) -> None:
    case_id = _high_risk_case(store, settings)
    client.headers.update(auth_headers(settings, sub="maya", role=role))
    assert client.post("/disputes", json={"case_id": case_id}).status_code == 202

    resp = client.post(f"/disputes/{case_id}/approve", json={"approved": True, "note": "mine"})
    assert resp.status_code == 403
    assert "maker-checker" in resp.json()["detail"]
    # Refused and recorded: a DENIED row with the reason, and no approval row.
    rows = [e for e in store.audit if e.server == "api" and e.tool == "approve_dispute"]
    assert [(e.actor, e.outcome, e.error) for e in rows] == [
        ("user:maya", AuditOutcome.DENIED, "maker-checker")
    ]
    assert "mine" not in rows[0].model_dump_json()  # nothing was decided, so no note is kept
    # The case is still waiting, and someone else can decide it.
    assert client.get(f"/disputes/{case_id}").json()["workflow"] == "awaiting_approval"
    client.headers.update(auth_headers(settings, sub="bea", role=Role.APPROVER))
    assert client.post(f"/disputes/{case_id}/approve", json={"approved": True}).status_code == 202
    assert client.get(f"/disputes/{case_id}").json()["workflow"] == "completed"


def test_the_case_shows_who_started_it(
    client: TestClient, store: InMemoryStore, settings: Settings
) -> None:
    case_id = _high_risk_case(store, settings)
    client.headers.update(auth_headers(settings, sub="maya", role=Role.APPROVER))
    client.post("/disputes", json={"case_id": case_id})
    assert client.get(f"/disputes/{case_id}").json()["started_by"] == "maya"


def test_a_case_with_no_recorded_starter_is_not_blocked(
    store: InMemoryStore, settings: Settings
) -> None:
    """A run begun by a caller with no identity (an eval, a script, a case from before the rule)
    has no starter on record. It must stay approvable rather than be stranded."""
    llm, _ = fake_llm()
    runtime = build_runtime(
        settings=settings,
        llm=llm,
        retriever=StubRetriever(),
        store=store,
        checkpointer=InMemorySaver(),
    )
    case_id = _high_risk_case(store, settings)
    runtime.claim_start(case_id)
    runtime.run(case_id)  # no started_by
    runtime.claim_resume(case_id, "anyone")  # does not raise
