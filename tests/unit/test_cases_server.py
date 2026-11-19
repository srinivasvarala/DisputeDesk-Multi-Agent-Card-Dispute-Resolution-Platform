from __future__ import annotations

from typing import Any

import pytest
from mcp import Client

from disputedesk.db.models import AuditOutcome, CaseStatus
from disputedesk.db.seed import REFERENCE_TIME
from disputedesk.mcp_servers.cases.server import READ_TOOLS, WRITE_TOOLS
from disputedesk.mcp_servers.cases.workflow import ALLOWED_TRANSITIONS, transition_error
from disputedesk.mcp_servers.common.audit import AuditedMCPServer
from disputedesk.mcp_servers.common.store import InMemoryStore
from tests.unit.mcp_helpers import TEST_ACTOR, call, error_text, last_audit, ok

pytestmark = pytest.mark.anyio

DRAFT: dict[str, Any] = {
    "decision": "accept",
    "rationale": "Shipment marked lost by the carrier; merchant has no proof of delivery.",
    "letter": "Dear merchant, we are reversing this charge because ...",
    "author": "resolution-agent",
}


def open_case(store: InMemoryStore) -> str:
    return next(c.case_id for c in store.cases.values() if c.status == CaseStatus.OPEN)


async def test_tools_are_split_into_read_and_write(cases_server: AuditedMCPServer) -> None:
    async with Client(cases_server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == READ_TOOLS | WRITE_TOOLS
    for name in READ_TOOLS:
        assert tools[name].annotations and tools[name].annotations.read_only_hint is True
    for name in WRITE_TOOLS:
        assert tools[name].annotations and tools[name].annotations.read_only_hint is False
    assert tools["update_status"].annotations.destructive_hint is True  # type: ignore[union-attr]
    assert tools["save_draft"].annotations.destructive_hint is False  # type: ignore[union-attr]


# --------------------------------------------------------------------------- get_case


async def test_get_case(cases_server: AuditedMCPServer, store: InMemoryStore) -> None:
    case_id = open_case(store)
    data = ok(await call(cases_server, "get_case", {"case_id": case_id}))
    assert data["case"]["case_id"] == case_id
    assert data["draft_count"] == 0
    assert data["latest_draft"] is None
    assert set(data["allowed_next_statuses"]) == {"in_review", "escalated"}
    entry = last_audit(store)
    assert (entry.outcome, entry.case_id, entry.actor) == (AuditOutcome.OK, case_id, TEST_ACTOR)


@pytest.mark.parametrize(
    ("args", "outcome"),
    [
        ({"case_id": "case_1"}, AuditOutcome.INVALID_INPUT),
        ({"case_id": "case_0001; DROP TABLE cases"}, AuditOutcome.INVALID_INPUT),
        ({}, AuditOutcome.INVALID_INPUT),
        ({"case_id": "case_9999"}, AuditOutcome.NOT_FOUND),
    ],
)
async def test_get_case_rejects_bad_input(
    cases_server: AuditedMCPServer, store: InMemoryStore, args: dict[str, object], outcome: str
) -> None:
    error_text(await call(cases_server, "get_case", args))
    assert last_audit(store).outcome == outcome


# --------------------------------------------------------------------------- save_draft


async def test_save_draft_appends_versions(
    cases_server: AuditedMCPServer, store: InMemoryStore
) -> None:
    case_id = open_case(store)
    first = ok(await call(cases_server, "save_draft", {"case_id": case_id, **DRAFT}))
    second = ok(
        await call(
            cases_server, "save_draft", {"case_id": case_id, **DRAFT, "decision": "escalate"}
        )
    )
    assert (first["version"], second["version"]) == (1, 2)
    assert first["created_at"].startswith(REFERENCE_TIME.date().isoformat())

    view = ok(await call(cases_server, "get_case", {"case_id": case_id}))
    assert view["draft_count"] == 2
    assert view["latest_draft"]["decision"] == "escalate"
    assert [d.version for d in store.list_drafts(case_id)] == [1, 2]


@pytest.mark.parametrize(
    ("override", "outcome", "message"),
    [
        ({"decision": "approve"}, AuditOutcome.INVALID_INPUT, "decision"),
        ({"rationale": ""}, AuditOutcome.INVALID_INPUT, "at least 1 character"),
        ({"letter": "x" * 8_001}, AuditOutcome.INVALID_INPUT, "at most 8000"),
        ({"author": "Robert'); --"}, AuditOutcome.INVALID_INPUT, "pattern"),
        ({"case_id": "case_9999"}, AuditOutcome.NOT_FOUND, "not found"),
        ({"version": 7}, AuditOutcome.INVALID_INPUT, "unknown argument"),
    ],
)
async def test_save_draft_rejects_bad_input(
    cases_server: AuditedMCPServer,
    store: InMemoryStore,
    override: dict[str, object],
    outcome: str,
    message: str,
) -> None:
    args = {"case_id": open_case(store), **DRAFT, **override}
    assert message in error_text(await call(cases_server, "save_draft", args))
    assert last_audit(store).outcome == outcome
    assert not store.drafts


async def test_save_draft_refused_on_resolved_case(
    cases_server: AuditedMCPServer, store: InMemoryStore
) -> None:
    case_id = open_case(store)
    store.cases[case_id] = store.cases[case_id].model_copy(
        update={"status": CaseStatus.RESOLVED_REJECTED}
    )
    text = error_text(await call(cases_server, "save_draft", {"case_id": case_id, **DRAFT}))
    assert "drafts are closed" in text
    assert last_audit(store).outcome == AuditOutcome.CONFLICT


# --------------------------------------------------------------------------- update_status


async def test_update_status_full_lifecycle(
    cases_server: AuditedMCPServer, store: InMemoryStore
) -> None:
    case_id = open_case(store)

    async def move(to: str) -> dict[str, Any]:
        args = {"case_id": case_id, "new_status": to, "reason": f"moving to {to}"}
        return ok(await call(cases_server, "update_status", args))

    assert (await move("in_review"))["previous_status"] == "open"
    ok(await call(cases_server, "save_draft", {"case_id": case_id, **DRAFT}))
    await move("pending_approval")
    change = await move("resolved_accepted")
    assert change["new_status"] == "resolved_accepted"
    assert store.cases[case_id].status == CaseStatus.RESOLVED_ACCEPTED
    assert store.cases[case_id].updated_at == REFERENCE_TIME
    # The reason is kept in the audit log.
    assert last_audit(store).arguments["reason"] == "moving to resolved_accepted"


@pytest.mark.parametrize(
    ("new_status", "message"),
    [
        ("resolved_accepted", "cannot move case from open to resolved_accepted"),
        ("pending_approval", "cannot move case from open to pending_approval"),
        ("open", "cannot move case from open to open"),
    ],
)
async def test_update_status_rejects_illegal_transition(
    cases_server: AuditedMCPServer, store: InMemoryStore, new_status: str, message: str
) -> None:
    case_id = open_case(store)
    args = {"case_id": case_id, "new_status": new_status, "reason": "skip ahead"}
    assert message in error_text(await call(cases_server, "update_status", args))
    assert last_audit(store).outcome == AuditOutcome.CONFLICT
    assert store.cases[case_id].status == CaseStatus.OPEN


async def test_update_status_requires_draft_before_resolution(
    cases_server: AuditedMCPServer, store: InMemoryStore
) -> None:
    case_id = open_case(store)
    ok(
        await call(
            cases_server,
            "update_status",
            {"case_id": case_id, "new_status": "in_review", "reason": "start"},
        )
    )
    args = {"case_id": case_id, "new_status": "resolved_rejected", "reason": "no draft"}
    assert "without a saved draft" in error_text(await call(cases_server, "update_status", args))
    assert store.cases[case_id].status == CaseStatus.IN_REVIEW


@pytest.mark.parametrize(
    ("args", "outcome"),
    [
        ({"case_id": "case_0001", "new_status": "closed", "reason": "x"}, "invalid_input"),
        ({"case_id": "case_0001", "new_status": "in_review", "reason": ""}, "invalid_input"),
        ({"case_id": "case_0001", "new_status": "in_review"}, "invalid_input"),
        ({"case_id": "case_9999", "new_status": "in_review", "reason": "x"}, "not_found"),
    ],
)
async def test_update_status_rejects_bad_input(
    cases_server: AuditedMCPServer, store: InMemoryStore, args: dict[str, object], outcome: str
) -> None:
    before = store.cases["case_0001"].status
    error_text(await call(cases_server, "update_status", args))
    assert last_audit(store).outcome == outcome
    assert store.cases["case_0001"].status == before


# --------------------------------------------------------------------------- workflow rules


def test_terminal_statuses_have_no_exits() -> None:
    for status in (CaseStatus.RESOLVED_ACCEPTED, CaseStatus.RESOLVED_REJECTED):
        assert not ALLOWED_TRANSITIONS[status]
        for target in CaseStatus:
            assert transition_error(status, target, has_draft=True)


def test_every_status_has_transition_rules() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(CaseStatus)
