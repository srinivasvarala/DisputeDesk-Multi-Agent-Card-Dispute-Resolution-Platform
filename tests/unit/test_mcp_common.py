"""AuditedMCPServer and InMemoryStore behaviour, independent of any specific server."""

from __future__ import annotations

from datetime import timedelta

import pytest

from disputedesk.db.models import AuditOutcome, CaseStatus, Decision
from disputedesk.db.seed import REFERENCE_TIME, generate
from disputedesk.mcp_servers.common.audit import AuditedMCPServer
from disputedesk.mcp_servers.common.errors import ConflictError, NotFoundError
from disputedesk.mcp_servers.common.params import CaseIdParam
from disputedesk.mcp_servers.common.store import InMemoryStore
from tests.unit.mcp_helpers import TEST_ACTOR, call, error_text, last_audit, ok

pytestmark = pytest.mark.anyio


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore(generate())


@pytest.fixture
def server(store: InMemoryStore) -> AuditedMCPServer:
    srv = AuditedMCPServer("probe", audit=store, clock=lambda: REFERENCE_TIME)

    @srv.tool()
    def echo(case_id: CaseIdParam) -> dict[str, str]:
        """Echo, or fail on demand."""
        if case_id == "case_0404":
            raise NotFoundError("case case_0404 not found")
        if case_id == "case_0409":
            raise ConflictError("not allowed")
        if case_id == "case_0500":
            raise RuntimeError("boom")
        return {"case_id": case_id}

    return srv


async def test_success_is_audited_with_actor_and_case(
    server: AuditedMCPServer, store: InMemoryStore
) -> None:
    assert ok(await call(server, "echo", {"case_id": "case_0001"})) == {"case_id": "case_0001"}
    entry = last_audit(store)
    assert entry.outcome == AuditOutcome.OK
    assert (entry.server, entry.tool, entry.actor) == ("probe", "echo", TEST_ACTOR)
    assert entry.case_id == "case_0001"
    assert entry.arguments == {"case_id": "case_0001"}
    assert entry.error is None
    assert entry.created_at == REFERENCE_TIME


@pytest.mark.parametrize(
    ("tool", "args", "outcome", "message"),
    [
        ("echo", {"case_id": "nope"}, AuditOutcome.INVALID_INPUT, "pattern"),
        ("echo", {}, AuditOutcome.INVALID_INPUT, "Field required"),
        ("echo", {"case_id": "case_0001", "x": 1}, AuditOutcome.INVALID_INPUT, "unknown argument"),
        ("missing", {}, AuditOutcome.INVALID_INPUT, "Unknown tool"),
        ("echo", {"case_id": "case_0404"}, AuditOutcome.NOT_FOUND, "not found"),
        ("echo", {"case_id": "case_0409"}, AuditOutcome.CONFLICT, "not allowed"),
        ("echo", {"case_id": "case_0500"}, AuditOutcome.ERROR, "Error executing tool echo"),
    ],
)
async def test_failures_are_audited(
    server: AuditedMCPServer,
    store: InMemoryStore,
    tool: str,
    args: dict[str, object],
    outcome: AuditOutcome,
    message: str,
) -> None:
    assert message in error_text(await call(server, tool, args))
    entry = last_audit(store)
    assert entry.outcome == outcome
    assert entry.tool == tool
    assert entry.error and message in entry.error
    assert len(store.audit) == 1


async def test_crash_details_reach_audit_but_not_client(
    server: AuditedMCPServer, store: InMemoryStore
) -> None:
    assert "boom" not in error_text(await call(server, "echo", {"case_id": "case_0500"}))
    assert "boom" in (last_audit(store).error or "")


async def test_audit_failure_fails_the_call(store: InMemoryStore) -> None:
    class BrokenSink:
        def write_audit(self, entry: object) -> None:
            raise OSError("audit store down")

    srv = AuditedMCPServer("probe", audit=BrokenSink())

    @srv.tool()
    def ping() -> str:
        return "pong"

    assert "audit store down" in error_text(await call(srv, "ping", {}))


# --------------------------------------------------------------------------- InMemoryStore


def test_list_transactions_filters_orders_and_pages(store: InMemoryStore) -> None:
    customer = store.list_transactions(limit=1)[0].customer_id
    rows = store.list_transactions(customer_id=customer)
    assert rows and all(t.customer_id == customer for t in rows)
    assert [t.posted_at for t in rows] == sorted((t.posted_at for t in rows), reverse=True)
    assert store.list_transactions(customer_id=customer, limit=1, offset=1) == rows[1:2]

    since = REFERENCE_TIME - timedelta(days=30)
    recent = store.list_transactions(since=since)
    assert recent and all(t.posted_at >= since for t in recent)


def test_add_draft_increments_version(store: InMemoryStore) -> None:
    kwargs = {"decision": Decision.ACCEPT, "rationale": "r", "letter": "l", "author": "a"}
    first = store.add_draft("case_0001", now=REFERENCE_TIME, **kwargs)  # type: ignore[arg-type]
    second = store.add_draft("case_0001", now=REFERENCE_TIME, **kwargs)  # type: ignore[arg-type]
    assert (first.version, second.version) == (1, 2)
    assert store.list_drafts("case_0001") == [first, second]
    with pytest.raises(NotFoundError):
        store.add_draft("case_9999", now=REFERENCE_TIME, **kwargs)  # type: ignore[arg-type]


def test_set_case_status_is_compare_and_set(store: InMemoryStore) -> None:
    current = store.cases["case_0002"].status
    updated = store.set_case_status(
        "case_0002", expected=current, new=CaseStatus.ESCALATED, now=REFERENCE_TIME
    )
    assert updated.status == CaseStatus.ESCALATED
    with pytest.raises(ConflictError):
        store.set_case_status(
            "case_0002", expected=current, new=CaseStatus.IN_REVIEW, now=REFERENCE_TIME
        )
