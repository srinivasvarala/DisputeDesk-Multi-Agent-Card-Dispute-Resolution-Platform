"""`merchant` MCP server: read-only merchant profile, dispute rate and delivery data.

Tools: get_profile, get_dispute_rate, get_delivery_status. None of them can change data.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Annotated

from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from disputedesk.db.models import Merchant, ReasonCode, ShipmentStatus
from disputedesk.mcp_servers.common.audit import AuditedMCPServer
from disputedesk.mcp_servers.common.errors import NotFoundError
from disputedesk.mcp_servers.common.params import Clock, MerchantIdParam, TxnIdParam, utc_now
from disputedesk.mcp_servers.common.store import AuditSink, ReadStore
from disputedesk.security.agent_tokens import CredentialVerifier, NoAuth

SERVER_NAME = "merchant"
DEFAULT_PORT = 8102
READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)


class DisputeRate(BaseModel):
    merchant_id: str
    window_start: datetime
    window_end: datetime
    txn_count: int
    dispute_count: int
    dispute_rate: float = Field(ge=0, le=1, description="dispute_count / txn_count; 0 if no txns")
    by_reason: dict[ReasonCode, int]


class DeliveryStatus(BaseModel):
    txn_id: str
    merchant_id: str
    applicable: bool = Field(description="False when the merchant does not ship physical goods")
    shipment_found: bool = Field(
        description="Whether a shipment record exists. applicable=True with shipment_found=False "
        "means the merchant ships goods but never shipped this order."
    )
    status: ShipmentStatus | None = None
    carrier: str | None = None
    tracking_number: str | None = None
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None


def build_server(
    store: ReadStore,
    audit: AuditSink,
    verifier: CredentialVerifier | NoAuth,
    clock: Clock = utc_now,
) -> AuditedMCPServer:
    server = AuditedMCPServer(
        SERVER_NAME,
        audit=audit,
        clock=clock,
        verifier=verifier,
        instructions="Read-only merchant lookups for dispute evidence. All data is synthetic.",
    )

    def require_merchant(merchant_id: str) -> Merchant:
        merchant = store.get_merchant(merchant_id)
        if merchant is None:
            raise NotFoundError(f"merchant {merchant_id} not found")
        return merchant

    @server.tool(annotations=READ_ONLY)
    def get_profile(merchant_id: MerchantIdParam) -> Merchant:
        """Get a merchant's profile: category, MCC, risk tier, whether it ships goods."""
        return require_merchant(merchant_id)

    @server.tool(annotations=READ_ONLY)
    def get_dispute_rate(
        merchant_id: MerchantIdParam,
        window_days: Annotated[int, Field(ge=1, le=365, description="Look-back window")] = 180,
    ) -> DisputeRate:
        """Share of a merchant's transactions in the window that were disputed."""
        require_merchant(merchant_id)
        end = clock()
        start = end - timedelta(days=window_days)
        txn_ids = {
            t.txn_id
            for t in store.list_transactions(merchant_id=merchant_id, since=start, until=end)
        }
        disputes = [c for c in store.list_cases(merchant_id=merchant_id) if c.txn_id in txn_ids]
        return DisputeRate(
            merchant_id=merchant_id,
            window_start=start,
            window_end=end,
            txn_count=len(txn_ids),
            dispute_count=len(disputes),
            dispute_rate=round(len(disputes) / len(txn_ids), 4) if txn_ids else 0.0,
            by_reason=dict(Counter(c.reason_code for c in disputes)),
        )

    @server.tool(annotations=READ_ONLY)
    def get_delivery_status(txn_id: TxnIdParam) -> DeliveryStatus:
        """Shipping and delivery status for a transaction, if the merchant ships goods."""
        txn = store.get_transaction(txn_id)
        if txn is None:
            raise NotFoundError(f"transaction {txn_id} not found")
        merchant = require_merchant(txn.merchant_id)
        shipment = store.get_shipment(txn_id)
        if shipment is None:
            return DeliveryStatus(
                txn_id=txn_id,
                merchant_id=txn.merchant_id,
                applicable=merchant.ships_physical_goods,
                shipment_found=False,
            )
        return DeliveryStatus(
            txn_id=txn_id,
            merchant_id=txn.merchant_id,
            applicable=True,
            shipment_found=True,
            status=shipment.status,
            carrier=shipment.carrier,
            tracking_number=shipment.tracking_number,
            shipped_at=shipment.shipped_at,
            delivered_at=shipment.delivered_at,
        )

    return server
