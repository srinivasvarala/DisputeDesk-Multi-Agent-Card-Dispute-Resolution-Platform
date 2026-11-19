"""PostgresStore must behave exactly like InMemoryStore (which the unit tests use)."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

import psycopg
import pytest

from disputedesk.db.models import AuditEntry, AuditOutcome, CaseStatus, Decision
from disputedesk.db.seed import REFERENCE_TIME, generate, load
from disputedesk.mcp_servers.common.errors import ConflictError, NotFoundError
from disputedesk.mcp_servers.common.store import InMemoryStore, PostgresStore

pytestmark = pytest.mark.integration


@pytest.fixture
def pg(conn: psycopg.Connection) -> PostgresStore:
    return PostgresStore(conn)


@pytest.fixture
def mem() -> InMemoryStore:
    return InMemoryStore(generate())


def test_seed_counts_and_reset_is_repeatable(conn: psycopg.Connection) -> None:
    counts = conn.execute(
        "SELECT (SELECT count(*) FROM customers), (SELECT count(*) FROM merchants),"
        " (SELECT count(*) FROM transactions), (SELECT count(*) FROM cases)"
    ).fetchone()
    assert counts == (50, 20, 200, 40)
    load(conn, generate(), reset=True)  # idempotent reload
    assert conn.execute("SELECT count(*) FROM transactions").fetchone() == (200,)
    with pytest.raises(RuntimeError, match="already seeded"):
        load(conn, generate())


def test_lookups_match_in_memory(pg: PostgresStore, mem: InMemoryStore) -> None:
    assert pg.get_customer("cus_0001") == mem.get_customer("cus_0001")
    assert pg.get_merchant("mer_001") == mem.get_merchant("mer_001")
    assert pg.get_transaction("txn_000150") == mem.get_transaction("txn_000150")
    assert pg.get_case("case_0010") == mem.get_case("case_0010")
    shipped = next(iter(mem.shipments))
    assert pg.get_shipment(shipped) == mem.get_shipment(shipped)
    assert pg.get_transaction("txn_999999") is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"customer_id": "cus_0026"},
        {"merchant_id": "mer_001"},
        {"merchant_id": "mer_001", "limit": 3, "offset": 2},
        {"customer_id": "cus_0026", "merchant_id": "mer_002"},
        {
            "since": REFERENCE_TIME - timedelta(days=60),
            "until": REFERENCE_TIME - timedelta(days=30),
        },
        {},
    ],
)
def test_list_transactions_matches_in_memory(
    pg: PostgresStore, mem: InMemoryStore, kwargs: dict[str, Any]
) -> None:
    assert pg.list_transactions(**kwargs) == mem.list_transactions(**kwargs)


@pytest.mark.parametrize("kwargs", [{}, {"customer_id": "cus_0040"}, {"merchant_id": "mer_001"}])
def test_list_cases_matches_in_memory(
    pg: PostgresStore, mem: InMemoryStore, kwargs: dict[str, Any]
) -> None:
    assert pg.list_cases(**kwargs) == mem.list_cases(**kwargs)


def _audit(tag: str, **overrides: Any) -> AuditEntry:
    fields: dict[str, Any] = {
        "server": "cases",
        "tool": "save_draft",
        "actor": tag,
        "case_id": None,
        "arguments": {},
        "outcome": AuditOutcome.OK,
        "error": None,
        "duration_ms": 0,
        "created_at": REFERENCE_TIME,
    }
    return AuditEntry(**{**fields, **overrides})


def _audit_count(conn: psycopg.Connection, actor: str) -> int:
    row = conn.execute("SELECT count(*) FROM audit_log WHERE actor = %s", (actor,)).fetchone()
    assert row is not None
    return int(row[0])


DRAFT: dict[str, Any] = {
    "decision": Decision.REJECT,
    "rationale": "r",
    "letter": "l",
    "author": "a",
    "now": REFERENCE_TIME,
}


def test_add_draft_versions_and_audits_atomically(
    pg: PostgresStore, conn: psycopg.Connection
) -> None:
    tag = f"it-{uuid.uuid4().hex[:8]}"
    assert pg.add_draft("case_0003", **DRAFT, audit=_audit(tag)).version == 1
    assert pg.add_draft("case_0003", **DRAFT, audit=_audit(tag)).version == 2
    assert [d.version for d in pg.list_drafts("case_0003")] == [1, 2]
    assert _audit_count(conn, tag) == 2
    with pytest.raises(NotFoundError):
        pg.add_draft("case_9999", **DRAFT, audit=_audit(tag))
    assert _audit_count(conn, tag) == 2


def test_set_case_status_compare_and_set(pg: PostgresStore, conn: psycopg.Connection) -> None:
    tag = f"it-{uuid.uuid4().hex[:8]}"
    current = pg.get_case("case_0004")
    assert current is not None
    updated = pg.set_case_status(
        "case_0004",
        expected=current.status,
        new=CaseStatus.ESCALATED,
        now=REFERENCE_TIME,
        audit=_audit(tag),
    )
    assert (updated.status, updated.updated_at) == (CaseStatus.ESCALATED, REFERENCE_TIME)
    with pytest.raises(ConflictError):
        pg.set_case_status(
            "case_0004",
            expected=current.status,
            new=CaseStatus.IN_REVIEW,
            now=REFERENCE_TIME,
            audit=_audit(tag),
        )
    with pytest.raises(NotFoundError):
        pg.set_case_status(
            "case_9999",
            expected=CaseStatus.OPEN,
            new=CaseStatus.IN_REVIEW,
            now=REFERENCE_TIME,
            audit=_audit(tag),
        )
    assert _audit_count(conn, tag) == 1


def test_write_rolls_back_when_its_audit_row_fails(pg: PostgresStore) -> None:
    # An argument that cannot be serialised to JSONB makes the audit INSERT fail.
    bad = _audit("it-bad", arguments={"x": object()})
    before = pg.get_case("case_0005")
    assert before is not None
    with pytest.raises(TypeError):
        pg.add_draft("case_0005", **DRAFT, audit=bad)
    with pytest.raises(TypeError):
        pg.set_case_status(
            "case_0005",
            expected=before.status,
            new=CaseStatus.ESCALATED,
            now=REFERENCE_TIME,
            audit=bad,
        )
    assert pg.list_drafts("case_0005") == []
    assert pg.get_case("case_0005") == before


def test_concurrent_writes_do_not_interleave(pg: PostgresStore, conn: psycopg.Connection) -> None:
    """Drafts, failing drafts and audit writes from many threads on one shared connection."""
    tag = f"it-{uuid.uuid4().hex[:8]}"

    def good(_: int) -> None:
        pg.add_draft("case_0006", **DRAFT, audit=_audit(tag))

    def missing(_: int) -> None:
        with pytest.raises(NotFoundError):
            pg.add_draft("case_9999", **DRAFT, audit=_audit(tag))

    def audit_only(_: int) -> None:
        pg.write_audit(_audit(tag, tool="get_case"))

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(fn, i) for i in range(20) for fn in (good, missing, audit_only)]
        for f in futures:
            f.result()

    assert [d.version for d in pg.list_drafts("case_0006")] == list(range(1, 21))
    assert _audit_count(conn, tag) == 40  # 20 drafts + 20 audit-only; failed drafts write none


def test_write_audit_persists_row(pg: PostgresStore, conn: psycopg.Connection) -> None:
    entry = AuditEntry(
        server="cases",
        tool="get_case",
        actor="integration-store-test",
        case_id="case_0001",
        arguments={"case_id": "case_0001", "nested": {"x": [1, 2]}},
        outcome=AuditOutcome.NOT_FOUND,
        error="case case_0001 not found",
        duration_ms=3,
        created_at=REFERENCE_TIME,
    )
    pg.write_audit(entry)
    row = conn.execute(
        "SELECT server, tool, case_id, arguments, outcome, error FROM audit_log"
        " WHERE actor = 'integration-store-test' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row == (
        "cases",
        "get_case",
        "case_0001",
        entry.arguments,
        "not_found",
        "case case_0001 not found",
    )
