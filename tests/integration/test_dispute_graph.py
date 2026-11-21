"""The dispute graph on real Postgres (app data, audit_log and LangGraph checkpoints) with the
deterministic fake LLM. Covers the three paths the milestone requires:

- happy path: a low-risk case resolves with no human involved;
- reviewer-rejection path: two revisions are allowed, a third request escalates;
- human approval: a high-risk case pauses at a checkpoint and resumes after `approve`,
  even on a brand-new runtime (as after an API restart).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from tests.conftest import make_settings
from tests.graph_helpers import (
    TEST_JWT_SECRET,
    StubRetriever,
    always_revise,
    auth_headers,
    authed_client,
    fake_llm,
    needs_approval,
)

from disputedesk.api.runtime import Runtime, build_runtime
from disputedesk.config import Settings
from disputedesk.db.models import Case, CaseStatus
from disputedesk.db.seed import generate
from disputedesk.graph.serde import checkpoint_serde
from disputedesk.mcp_servers.common.store import InMemoryStore, PostgresStore
from disputedesk.security.scopes import Role

pytestmark = pytest.mark.integration

CHECKPOINT_TABLES = "checkpoints, checkpoint_blobs, checkpoint_writes"

type Factory = Callable[..., tuple[TestClient, list[str]]]


@pytest.fixture
def pool(database_url: str, conn: psycopg.Connection) -> Iterator[ConnectionPool]:
    """Checkpointer pool on a database whose app data was just reseeded (`conn`) and whose
    old checkpoints are cleared, so every test starts from scratch."""
    with ConnectionPool(
        database_url,
        min_size=1,
        max_size=3,
        kwargs={"autocommit": True, "row_factory": dict_row},
        open=True,
    ) as p:
        PostgresSaver(p).setup()  # type: ignore[arg-type]
        conn.execute(f"TRUNCATE {CHECKPOINT_TABLES}")
        yield p


@pytest.fixture
def settings() -> Settings:
    return make_settings(jwt_secret=TEST_JWT_SECRET)


@pytest.fixture
def make_client(conn: psycopg.Connection, pool: ConnectionPool, settings: Settings) -> Factory:
    """Each call builds a brand-new runtime (graph, agents, checkpointer) on the same database."""

    def make(**llm_args: Any) -> tuple[TestClient, list[str]]:
        llm, calls = fake_llm(**llm_args)
        runtime: Runtime = build_runtime(
            settings=settings,
            llm=llm,
            retriever=StubRetriever(),
            store=PostgresStore(conn),
            checkpointer=PostgresSaver(pool, serde=checkpoint_serde()),  # type: ignore[arg-type]
        )
        return authed_client(runtime, settings), calls

    return make


def pick_case(settings: Settings, *, approval: bool) -> Case:
    """An open seeded case that does (or does not) need a human, chosen with the same rules the
    router applies."""
    data = InMemoryStore(generate())
    return next(
        c
        for c in data.list_cases()
        if c.status == CaseStatus.OPEN and needs_approval(c, data, settings) == approval
    )


def last_audit_id(conn: psycopg.Connection) -> int:
    """audit_log is never reset by the seed, so tests only look at rows written after this."""
    row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM audit_log").fetchone()
    assert row is not None
    return int(row[0])


def audit_tools(conn: psycopg.Connection, since_id: int) -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT actor, tool FROM audit_log WHERE id > %s ORDER BY id", (since_id,)
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def db_status(conn: psycopg.Connection, case_id: str) -> str:
    row = conn.execute("SELECT status FROM cases WHERE case_id = %s", (case_id,)).fetchone()
    assert row is not None
    return str(row[0])


def test_happy_path_resolves_without_a_human(
    make_client: Factory, conn: psycopg.Connection, settings: Settings
) -> None:
    client, calls = make_client()
    case = pick_case(settings, approval=False)

    assert client.post("/disputes", json={"case_id": case.case_id}).status_code == 202
    body = client.get(f"/disputes/{case.case_id}").json()

    assert body["workflow"] == "completed"
    assert body["path"] == [
        "intake",
        "router",
        "evidence",
        "policy",
        "resolution",
        "reviewer",
        "output_check",
        "finalize",
    ]
    assert body["case_status"] in ("resolved_accepted", "resolved_rejected")
    assert db_status(conn, case.case_id) == body["case_status"]
    assert calls == ["router", "evidence", "policy", "resolution", "reviewer"]
    draft = conn.execute(
        "SELECT version, author FROM case_drafts WHERE case_id = %s", (case.case_id,)
    ).fetchall()
    assert draft == [(1, "resolution_agent")]


def test_every_step_is_audited_under_the_right_agent(
    make_client: Factory, conn: psycopg.Connection, settings: Settings
) -> None:
    client, _ = make_client()
    case = pick_case(settings, approval=False)
    since = last_audit_id(conn)
    client.post("/disputes", json={"case_id": case.case_id})

    trail = audit_tools(conn, since)
    assert ("dispute_workflow", "get_case") in trail
    assert ("dispute_workflow", "update_status") in trail
    assert ("router_agent", "classify_dispute") in trail
    assert ("evidence_agent", "get_history") in trail  # a tool call
    assert ("evidence_agent", "assess_evidence") in trail  # and its decision
    assert ("policy_agent", "search_policies") in trail
    assert ("resolution_agent", "save_draft") in trail
    assert ("reviewer_agent", "review_draft") in trail
    # Agents only ever show up with tools from their own allowlist.
    assert not any(a == "reviewer_agent" and t not in {"review_draft"} for a, t in trail)
    assert not any(a == "router_agent" and t == "save_draft" for a, t in trail)


def test_reviewer_revises_once_then_approves(
    make_client: Factory, conn: psycopg.Connection, settings: Settings
) -> None:
    from tests.graph_helpers import REVISE

    client, calls = make_client(reviews=[REVISE])
    case = pick_case(settings, approval=False)
    client.post("/disputes", json={"case_id": case.case_id})

    body = client.get(f"/disputes/{case.case_id}").json()
    assert body["revision_count"] == 1
    assert body["draft"]["version"] == 2
    assert body["case_status"].startswith("resolved_")
    assert body["path"].count("resolution") == 2
    assert calls.count("resolution") == 2
    versions = conn.execute(
        "SELECT version FROM case_drafts WHERE case_id = %s ORDER BY version", (case.case_id,)
    ).fetchall()
    assert versions == [(1,), (2,)]  # both drafts are kept


def test_reviewer_rejection_path_escalates_after_two_revisions(
    make_client: Factory, conn: psycopg.Connection, settings: Settings
) -> None:
    client, calls = make_client(reviews=always_revise())
    case = pick_case(settings, approval=False)
    client.post("/disputes", json={"case_id": case.case_id})

    body = client.get(f"/disputes/{case.case_id}").json()
    assert body["case_status"] == "escalated"
    assert body["revision_count"] == 3
    assert body["draft"]["version"] == 3  # the first draft plus exactly two revisions
    assert body["path"][-2:] == ["reviewer", "escalate"]
    assert calls.count("resolution") == 3
    assert db_status(conn, case.case_id) == "escalated"


def test_high_risk_case_pauses_and_resumes_after_restart(
    make_client: Factory, conn: psycopg.Connection, settings: Settings
) -> None:
    case = pick_case(settings, approval=True)

    first, _ = make_client()
    first.post("/disputes", json={"case_id": case.case_id})
    paused = first.get(f"/disputes/{case.case_id}").json()
    assert paused["workflow"] == "awaiting_approval"
    assert paused["case_status"] == "pending_approval"
    assert db_status(conn, case.case_id) == "pending_approval"

    # A different runtime (as after a restart) sees the pause only through the checkpoint.
    second, calls = make_client()
    seen = second.get(f"/disputes/{case.case_id}").json()
    assert seen["workflow"] == "awaiting_approval"
    assert seen["started_by"] == "ana"  # the starter is in the checkpoint, so it survives

    # Maker-checker still holds after the restart: the starter ("ana") cannot approve.
    own = second.post(
        f"/disputes/{case.case_id}/approve",
        json={"approved": True},
        headers=auth_headers(settings, sub="ana", role=Role.APPROVER),
    )
    assert own.status_code == 403

    resp = second.post(
        f"/disputes/{case.case_id}/approve",
        json={"approved": True, "note": "checked"},
    )
    assert resp.status_code == 202
    done = second.get(f"/disputes/{case.case_id}").json()
    assert done["workflow"] == "completed"
    assert done["case_status"].startswith("resolved_")
    assert done["path"][-3:] == ["request_approval", "human_gate", "finalize"]
    assert calls == []  # resuming must not call any agent again
    reason = conn.execute(
        "SELECT arguments->>'reason' FROM audit_log "
        "WHERE case_id = %s AND tool = 'update_status' ORDER BY id DESC LIMIT 1",
        (case.case_id,),
    ).fetchone()
    assert reason is not None
    assert "bea" in reason[0]  # the approver, not the user who started the case


def test_human_can_decline_and_the_case_escalates(
    make_client: Factory, conn: psycopg.Connection, settings: Settings
) -> None:
    client, _ = make_client()
    case = pick_case(settings, approval=True)
    client.post("/disputes", json={"case_id": case.case_id})
    client.post(
        f"/disputes/{case.case_id}/approve",
        json={"approved": False, "note": "not enough evidence"},
    )
    assert db_status(conn, case.case_id) == "escalated"


def test_five_seeded_disputes_complete_end_to_end(
    make_client: Factory, conn: psycopg.Connection, settings: Settings
) -> None:
    """Milestone acceptance: five seeded disputes each finish; those needing approval pause and
    finish once approved."""
    client, _ = make_client()
    data = InMemoryStore(generate())
    open_cases = [c for c in data.list_cases() if c.status == CaseStatus.OPEN]
    low = [c for c in open_cases if not needs_approval(c, data, settings)][:3]
    high = [c for c in open_cases if needs_approval(c, data, settings)][:2]
    assert len(low) == 3 and len(high) == 2

    for case in [*low, *high]:
        assert client.post("/disputes", json={"case_id": case.case_id}).status_code == 202
    for case in high:
        assert client.get(f"/disputes/{case.case_id}").json()["workflow"] == "awaiting_approval"
        client.post(f"/disputes/{case.case_id}/approve", json={"approved": True})
    for case in [*low, *high]:
        body = client.get(f"/disputes/{case.case_id}").json()
        assert body["workflow"] == "completed"
        assert body["case_status"].startswith("resolved_")
