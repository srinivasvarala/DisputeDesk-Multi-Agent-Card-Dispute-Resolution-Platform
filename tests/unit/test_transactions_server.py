from __future__ import annotations

from collections import Counter
from datetime import timedelta

import pytest

from disputedesk.db.models import AuditOutcome
from disputedesk.db.seed import REFERENCE_TIME
from disputedesk.mcp_servers.common.audit import AuditedMCPServer
from disputedesk.mcp_servers.common.store import InMemoryStore
from tests.unit.mcp_helpers import call, error_text, last_audit, list_tool_names, ok

pytestmark = pytest.mark.anyio


def busiest_customer(store: InMemoryStore) -> str:
    return Counter(t.customer_id for t in store.transactions.values()).most_common(1)[0][0]


async def test_lists_exactly_its_tools(txn_server: AuditedMCPServer) -> None:
    assert await list_tool_names(txn_server) == {"get_txn", "list_txns", "get_history"}


# --------------------------------------------------------------------------- get_txn


async def test_get_txn(txn_server: AuditedMCPServer, store: InMemoryStore) -> None:
    data = ok(await call(txn_server, "get_txn", {"txn_id": "txn_000001"}))
    expected = store.transactions["txn_000001"]
    assert data["txn_id"] == "txn_000001"
    assert data["amount_cents"] == expected.amount_cents
    assert last_audit(store).outcome == AuditOutcome.OK


@pytest.mark.parametrize(
    ("args", "outcome"),
    [
        ({"txn_id": "txn_1"}, AuditOutcome.INVALID_INPUT),
        ({"txn_id": "TXN_000001"}, AuditOutcome.INVALID_INPUT),
        ({"txn_id": 1}, AuditOutcome.INVALID_INPUT),
        ({}, AuditOutcome.INVALID_INPUT),
        ({"txn_id": "txn_999999"}, AuditOutcome.NOT_FOUND),
    ],
)
async def test_get_txn_rejects_bad_input(
    txn_server: AuditedMCPServer, store: InMemoryStore, args: dict[str, object], outcome: str
) -> None:
    error_text(await call(txn_server, "get_txn", args))
    assert last_audit(store).outcome == outcome


# --------------------------------------------------------------------------- list_txns


async def test_list_txns_by_customer_pages(
    txn_server: AuditedMCPServer, store: InMemoryStore
) -> None:
    customer = busiest_customer(store)
    total = sum(t.customer_id == customer for t in store.transactions.values())
    first = ok(await call(txn_server, "list_txns", {"customer_id": customer, "limit": 2}))
    assert len(first["items"]) == 2
    assert first["has_more"] is (total > 2)
    assert all(i["customer_id"] == customer for i in first["items"])

    rest = ok(
        await call(txn_server, "list_txns", {"customer_id": customer, "offset": 2, "limit": 100})
    )
    assert len(rest["items"]) == total - 2
    assert rest["has_more"] is False
    ids = [i["txn_id"] for i in first["items"] + rest["items"]]
    assert len(set(ids)) == total


async def test_list_txns_by_merchant_and_dates(
    txn_server: AuditedMCPServer, store: InMemoryStore
) -> None:
    since = (REFERENCE_TIME - timedelta(days=60)).date()
    until = (REFERENCE_TIME - timedelta(days=30)).date()
    args = {"merchant_id": "mer_001", "since": since.isoformat(), "until": until.isoformat()}
    data = ok(await call(txn_server, "list_txns", {**args, "limit": 100}))
    expected = [
        t
        for t in store.transactions.values()
        if t.merchant_id == "mer_001" and since <= t.posted_at.date() <= until
    ]
    assert {i["txn_id"] for i in data["items"]} == {t.txn_id for t in expected}


@pytest.mark.parametrize(
    ("args", "outcome", "message"),
    [
        ({}, AuditOutcome.INVALID_INPUT, "provide customer_id"),
        ({"customer_id": "cus_1"}, AuditOutcome.INVALID_INPUT, "pattern"),
        ({"customer_id": "cus_0001", "limit": 0}, AuditOutcome.INVALID_INPUT, "greater than"),
        ({"customer_id": "cus_0001", "limit": 101}, AuditOutcome.INVALID_INPUT, "less than"),
        ({"customer_id": "cus_0001", "offset": -1}, AuditOutcome.INVALID_INPUT, "greater than"),
        ({"customer_id": "cus_0001", "since": "yesterday"}, AuditOutcome.INVALID_INPUT, "date"),
        ({"customer_id": "cus_0001", "until": "9999-12-31"}, AuditOutcome.INVALID_INPUT, "2100"),
        ({"customer_id": "cus_0001", "since": "0001-01-01"}, AuditOutcome.INVALID_INPUT, "2000"),
        (
            {"customer_id": "cus_0001", "since": "2026-10-02", "until": "2026-10-01"},
            AuditOutcome.INVALID_INPUT,
            "since must be on or before until",
        ),
        ({"customer_id": "cus_9999"}, AuditOutcome.NOT_FOUND, "customer cus_9999 not found"),
        ({"merchant_id": "mer_999"}, AuditOutcome.NOT_FOUND, "merchant mer_999 not found"),
    ],
)
async def test_list_txns_rejects_bad_input(
    txn_server: AuditedMCPServer,
    store: InMemoryStore,
    args: dict[str, object],
    outcome: str,
    message: str,
) -> None:
    assert message in error_text(await call(txn_server, "list_txns", args))
    assert last_audit(store).outcome == outcome


# --------------------------------------------------------------------------- get_history


async def test_get_history_summarises_window(
    txn_server: AuditedMCPServer, store: InMemoryStore
) -> None:
    customer = busiest_customer(store)
    data = ok(await call(txn_server, "get_history", {"customer_id": customer, "days": 730}))
    txns = [t for t in store.transactions.values() if t.customer_id == customer]
    assert data["txn_count"] == len(txns)
    assert data["total_spend_cents"] == sum(t.amount_cents for t in txns)
    assert data["distinct_merchants"] == len({t.merchant_id for t in txns})
    assert data["dispute_count"] == sum(c.customer_id == customer for c in store.cases.values())
    assert len(data["top_merchants"]) <= 5
    counts = [m["txn_count"] for m in data["top_merchants"]]
    assert counts == sorted(counts, reverse=True)


async def test_get_history_narrow_window_excludes_old(
    txn_server: AuditedMCPServer, store: InMemoryStore
) -> None:
    customer = busiest_customer(store)
    data = ok(await call(txn_server, "get_history", {"customer_id": customer, "days": 1}))
    assert data["txn_count"] == 0  # seed data ends 10+ days before the reference time
    assert data["first_txn_at"] is None


@pytest.mark.parametrize(
    ("args", "outcome"),
    [
        ({"customer_id": "cus_0001", "days": 0}, AuditOutcome.INVALID_INPUT),
        ({"customer_id": "cus_0001", "days": 731}, AuditOutcome.INVALID_INPUT),
        ({"customer_id": "nobody"}, AuditOutcome.INVALID_INPUT),
        ({"customer_id": "cus_9999"}, AuditOutcome.NOT_FOUND),
    ],
)
async def test_get_history_rejects_bad_input(
    txn_server: AuditedMCPServer, store: InMemoryStore, args: dict[str, object], outcome: str
) -> None:
    error_text(await call(txn_server, "get_history", args))
    assert last_audit(store).outcome == outcome
