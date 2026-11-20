"""AuditedMCPServer and InMemoryStore behaviour, independent of any specific server."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import anyio
import pytest

from disputedesk.db.models import AuditEntry, AuditOutcome, CaseStatus, Decision
from disputedesk.db.seed import REFERENCE_TIME, generate
from disputedesk.mcp_servers.common.audit import AuditedMCPServer, audited_write
from disputedesk.mcp_servers.common.errors import ConflictError, NotFoundError
from disputedesk.mcp_servers.common.params import CaseIdParam
from disputedesk.mcp_servers.common.store import InMemoryStore
from disputedesk.security.agent_tokens import NO_AUTH
from tests.unit.mcp_helpers import TEST_ACTOR, call, error_text, last_audit, ok

pytestmark = pytest.mark.anyio


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore(generate())


@pytest.fixture
def server(store: InMemoryStore) -> AuditedMCPServer:
    srv = AuditedMCPServer("probe", audit=store, clock=lambda: REFERENCE_TIME, verifier=NO_AUTH)

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


async def test_audit_failure_fails_the_call_with_generic_message() -> None:
    class BrokenSink:
        def write_audit(self, entry: object) -> None:
            raise OSError("db password=hunter2 at 10.0.0.5")

    srv = AuditedMCPServer("probe", audit=BrokenSink(), verifier=NO_AUTH)

    @srv.tool()
    def ping() -> str:
        return "pong"

    text = error_text(await call(srv, "ping", {}))
    assert "audit log unavailable" in text
    assert "hunter2" not in text and "10.0.0.5" not in text


async def test_cancelled_call_is_audited_as_cancelled(store: InMemoryStore) -> None:
    srv = AuditedMCPServer("probe", audit=store, clock=lambda: REFERENCE_TIME, verifier=NO_AUTH)

    @srv.tool()
    async def slow() -> str:
        await anyio.sleep(10)
        return "done"

    with anyio.move_on_after(0.05):
        await srv.call_tool("slow", {})
    entry = last_audit(store)
    assert (entry.tool, entry.outcome) == ("slow", AuditOutcome.CANCELLED)
    assert len(store.audit) == 1


def test_audited_write_outside_a_call_is_refused() -> None:
    with pytest.raises(RuntimeError, match="outside an AuditedMCPServer"):
        audited_write(lambda audit: audit)


# --------------------------------------------------------------------------- InMemoryStore


def _audit_entry() -> AuditEntry:
    return AuditEntry(
        server="cases",
        tool="save_draft",
        actor=TEST_ACTOR,
        case_id="case_0001",
        arguments={},
        outcome=AuditOutcome.OK,
        error=None,
        duration_ms=0,
        created_at=REFERENCE_TIME,
    )


DRAFT_KWARGS: dict[str, Any] = {
    "decision": Decision.ACCEPT,
    "rationale": "r",
    "letter": "l",
    "author": "a",
    "now": REFERENCE_TIME,
}


def test_list_transactions_filters_orders_and_pages(store: InMemoryStore) -> None:
    customer = store.list_transactions(limit=1)[0].customer_id
    rows = store.list_transactions(customer_id=customer)
    assert rows and all(t.customer_id == customer for t in rows)
    assert [t.posted_at for t in rows] == sorted((t.posted_at for t in rows), reverse=True)
    assert store.list_transactions(customer_id=customer, limit=1, offset=1) == rows[1:2]

    since = REFERENCE_TIME - timedelta(days=30)
    recent = store.list_transactions(since=since)
    assert recent and all(t.posted_at >= since for t in recent)


def test_add_draft_increments_version_and_writes_audit(store: InMemoryStore) -> None:
    first = store.add_draft("case_0001", **DRAFT_KWARGS, audit=_audit_entry())
    second = store.add_draft("case_0001", **DRAFT_KWARGS, audit=_audit_entry())
    assert (first.version, second.version) == (1, 2)
    assert store.list_drafts("case_0001") == [first, second]
    assert len(store.audit) == 2
    with pytest.raises(NotFoundError):
        store.add_draft("case_9999", **DRAFT_KWARGS, audit=_audit_entry())


def test_set_case_status_is_compare_and_set(store: InMemoryStore) -> None:
    current = store.cases["case_0002"].status
    updated = store.set_case_status(
        "case_0002",
        expected=current,
        new=CaseStatus.ESCALATED,
        now=REFERENCE_TIME,
        audit=_audit_entry(),
    )
    assert updated.status == CaseStatus.ESCALATED
    with pytest.raises(ConflictError):
        store.set_case_status(
            "case_0002",
            expected=current,
            new=CaseStatus.IN_REVIEW,
            now=REFERENCE_TIME,
            audit=_audit_entry(),
        )
    assert len(store.audit) == 1


def test_write_is_not_applied_when_its_audit_fails() -> None:
    class FailingAudit(InMemoryStore):
        def write_audit(self, entry: AuditEntry) -> None:
            raise OSError("audit down")

    store = FailingAudit(generate())
    before = store.cases["case_0002"]
    with pytest.raises(OSError):
        store.add_draft("case_0002", **DRAFT_KWARGS, audit=_audit_entry())
    with pytest.raises(OSError):
        store.set_case_status(
            "case_0002",
            expected=before.status,
            new=CaseStatus.ESCALATED,
            now=REFERENCE_TIME,
            audit=_audit_entry(),
        )
    assert store.list_drafts("case_0002") == []
    assert store.cases["case_0002"] == before


def test_a_server_without_a_verifier_choice_cannot_be_built() -> None:
    """Credential checking has no silent default: NO_AUTH must be asked for."""
    with pytest.raises(TypeError, match="verifier"):
        AuditedMCPServer("probe", audit=InMemoryStore(generate()))  # type: ignore[call-arg]
