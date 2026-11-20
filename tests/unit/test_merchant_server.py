from __future__ import annotations

import pytest

from disputedesk.db.models import AuditOutcome
from disputedesk.mcp_servers.common.audit import AuditedMCPServer
from disputedesk.mcp_servers.common.store import InMemoryStore
from tests.unit.mcp_helpers import call, error_text, last_audit, list_tool_names, ok

pytestmark = pytest.mark.anyio


async def test_lists_exactly_its_tools(merchant_server: AuditedMCPServer) -> None:
    assert await list_tool_names(merchant_server) == {
        "get_profile",
        "get_dispute_rate",
        "get_delivery_status",
    }


# --------------------------------------------------------------------------- get_profile


async def test_get_profile(merchant_server: AuditedMCPServer, store: InMemoryStore) -> None:
    data = ok(await call(merchant_server, "get_profile", {"merchant_id": "mer_001"}))
    expected = store.merchants["mer_001"]
    assert data["name"] == expected.name
    assert data["risk_tier"] == expected.risk_tier
    assert last_audit(store).outcome == AuditOutcome.OK


@pytest.mark.parametrize(
    ("args", "outcome"),
    [
        ({"merchant_id": "mer_1"}, AuditOutcome.INVALID_INPUT),
        ({"merchant_id": None}, AuditOutcome.INVALID_INPUT),
        ({"merchant_id": "mer_001", "verbose": True}, AuditOutcome.INVALID_INPUT),
        ({"merchant_id": "mer_999"}, AuditOutcome.NOT_FOUND),
    ],
)
async def test_get_profile_rejects_bad_input(
    merchant_server: AuditedMCPServer,
    store: InMemoryStore,
    args: dict[str, object],
    outcome: str,
) -> None:
    error_text(await call(merchant_server, "get_profile", args))
    assert last_audit(store).outcome == outcome


# --------------------------------------------------------------------------- get_dispute_rate


async def test_get_dispute_rate(merchant_server: AuditedMCPServer, store: InMemoryStore) -> None:
    data = ok(
        await call(
            merchant_server, "get_dispute_rate", {"merchant_id": "mer_001", "window_days": 365}
        )
    )
    txn_ids = {t.txn_id for t in store.transactions.values() if t.merchant_id == "mer_001"}
    disputes = [c for c in store.cases.values() if c.txn_id in txn_ids]
    assert data["txn_count"] == len(txn_ids)
    assert data["dispute_count"] == len(disputes) > 0
    assert data["dispute_rate"] == round(len(disputes) / len(txn_ids), 4)
    assert sum(data["by_reason"].values()) == len(disputes)


async def test_high_risk_merchant_has_higher_dispute_rate(
    merchant_server: AuditedMCPServer, store: InMemoryStore
) -> None:
    async def rate(merchant_id: str) -> float:
        args = {"merchant_id": merchant_id, "window_days": 365}
        return float(ok(await call(merchant_server, "get_dispute_rate", args))["dispute_rate"])

    high = [m for m in store.merchants.values() if m.risk_tier == "high"]
    low = [m for m in store.merchants.values() if m.risk_tier == "low"]
    avg_high = sum([await rate(m.merchant_id) for m in high]) / len(high)
    avg_low = sum([await rate(m.merchant_id) for m in low]) / len(low)
    assert avg_high > avg_low


async def test_get_dispute_rate_empty_window_is_zero(merchant_server: AuditedMCPServer) -> None:
    args = {"merchant_id": "mer_001", "window_days": 1}
    data = ok(await call(merchant_server, "get_dispute_rate", args))
    assert (data["txn_count"], data["dispute_rate"]) == (0, 0.0)


@pytest.mark.parametrize(
    ("args", "outcome"),
    [
        ({"merchant_id": "mer_001", "window_days": 0}, AuditOutcome.INVALID_INPUT),
        ({"merchant_id": "mer_001", "window_days": 366}, AuditOutcome.INVALID_INPUT),
        ({"merchant_id": "mer_001", "window_days": "a lot"}, AuditOutcome.INVALID_INPUT),
        ({"merchant_id": "mer_999"}, AuditOutcome.NOT_FOUND),
    ],
)
async def test_get_dispute_rate_rejects_bad_input(
    merchant_server: AuditedMCPServer,
    store: InMemoryStore,
    args: dict[str, object],
    outcome: str,
) -> None:
    error_text(await call(merchant_server, "get_dispute_rate", args))
    assert last_audit(store).outcome == outcome


# --------------------------------------------------------------------------- get_delivery_status


async def test_get_delivery_status_for_shipped_goods(
    merchant_server: AuditedMCPServer, store: InMemoryStore
) -> None:
    shipment = next(s for s in store.shipments.values() if s.status == "delivered")
    data = ok(await call(merchant_server, "get_delivery_status", {"txn_id": shipment.txn_id}))
    assert data["applicable"] is True
    assert data["shipment_found"] is True
    assert data["status"] == "delivered"
    assert data["tracking_number"] == shipment.tracking_number
    assert data["delivered_at"] is not None


async def test_get_delivery_status_not_applicable_for_services(
    merchant_server: AuditedMCPServer, store: InMemoryStore
) -> None:
    txn = next(
        t
        for t in store.transactions.values()
        if not store.merchants[t.merchant_id].ships_physical_goods
    )
    data = ok(await call(merchant_server, "get_delivery_status", {"txn_id": txn.txn_id}))
    assert (data["applicable"], data["shipment_found"], data["status"]) == (False, False, None)


async def test_get_delivery_status_goods_never_shipped(
    merchant_server: AuditedMCPServer, store: InMemoryStore
) -> None:
    # Duplicate charges at goods merchants have no shipment: shipping applies, none exists.
    txn = next(
        t
        for t in store.transactions.values()
        if store.merchants[t.merchant_id].ships_physical_goods and t.txn_id not in store.shipments
    )
    data = ok(await call(merchant_server, "get_delivery_status", {"txn_id": txn.txn_id}))
    assert (data["applicable"], data["shipment_found"], data["status"]) == (True, False, None)


@pytest.mark.parametrize(
    ("args", "outcome"),
    [
        ({"txn_id": "shp_000001"}, AuditOutcome.INVALID_INPUT),
        ({}, AuditOutcome.INVALID_INPUT),
        ({"txn_id": "txn_999999"}, AuditOutcome.NOT_FOUND),
    ],
)
async def test_get_delivery_status_rejects_bad_input(
    merchant_server: AuditedMCPServer,
    store: InMemoryStore,
    args: dict[str, object],
    outcome: str,
) -> None:
    error_text(await call(merchant_server, "get_delivery_status", args))
    assert last_audit(store).outcome == outcome


async def test_get_delivery_status_reports_the_carrier_facts_the_rulebook_needs(
    merchant_server: AuditedMCPServer, store: InMemoryStore
) -> None:
    """Rulebook 04: delivery proof needs an approved carrier, a matching address and, above
    $250.00, a signature. An unrecorded fact stays null, never false."""
    shipment = next(s for s in store.shipments.values() if s.status == "delivered")
    store.shipments[shipment.txn_id] = shipment.model_copy(
        update={
            "carrier": "QuickPost",
            "delivery_address_matches": False,
            "signature_confirmed": None,
        }
    )
    data = ok(await call(merchant_server, "get_delivery_status", {"txn_id": shipment.txn_id}))
    assert data["carrier_approved"] is False
    assert data["delivery_address_matches"] is False
    assert data["signature_confirmed"] is None


async def test_approved_carrier_is_flagged(
    merchant_server: AuditedMCPServer, store: InMemoryStore
) -> None:
    shipment = next(s for s in store.shipments.values() if s.carrier in ("ParcelPath", "SwiftShip"))
    data = ok(await call(merchant_server, "get_delivery_status", {"txn_id": shipment.txn_id}))
    assert data["carrier_approved"] is True
