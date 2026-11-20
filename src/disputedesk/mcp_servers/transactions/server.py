"""`transactions` MCP server: read-only access to card transactions.

Tools: get_txn, list_txns, get_history. None of them can change data.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated

from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from disputedesk.db.models import Transaction, TxnStatus
from disputedesk.mcp_servers.common.audit import AuditedMCPServer
from disputedesk.mcp_servers.common.errors import InvalidRequestError, NotFoundError
from disputedesk.mcp_servers.common.params import (
    Clock,
    CustomerIdParam,
    MerchantIdParam,
    TxnIdParam,
    utc_now,
)
from disputedesk.mcp_servers.common.store import AuditSink, ReadStore
from disputedesk.security.agent_tokens import CredentialVerifier, NoAuth

SERVER_NAME = "transactions"
DEFAULT_PORT = 8101
# Bounds keep date arithmetic safe (date.max + 1 day overflows) and reject nonsense ranges.
MIN_DATE = date(2000, 1, 1)
MAX_DATE = date(2100, 12, 31)
READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)


class TxnPage(BaseModel):
    items: list[Transaction]
    limit: int
    offset: int
    has_more: bool


class MerchantSpend(BaseModel):
    merchant_id: str
    txn_count: int
    total_cents: int


class CustomerHistory(BaseModel):
    customer_id: str
    window_start: datetime
    window_end: datetime
    txn_count: int
    total_spend_cents: int
    refunded_count: int
    distinct_merchants: int
    top_merchants: list[MerchantSpend] = Field(description="Up to 5, by transaction count")
    dispute_count: int = Field(description="Disputes opened by this customer in the window")
    first_txn_at: datetime | None
    last_txn_at: datetime | None


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
        instructions="Read-only card transaction lookups for dispute evidence. Synthetic data.",
    )

    @server.tool(annotations=READ_ONLY)
    def get_txn(txn_id: TxnIdParam) -> Transaction:
        """Get one transaction by ID."""
        txn = store.get_transaction(txn_id)
        if txn is None:
            raise NotFoundError(f"transaction {txn_id} not found")
        return txn

    @server.tool(annotations=READ_ONLY)
    def list_txns(
        customer_id: CustomerIdParam | None = None,
        merchant_id: MerchantIdParam | None = None,
        since: Annotated[
            date | None, Field(ge=MIN_DATE, le=MAX_DATE, description="Earliest posting date (UTC)")
        ] = None,
        until: Annotated[
            date | None, Field(ge=MIN_DATE, le=MAX_DATE, description="Latest posting date (UTC)")
        ] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        offset: Annotated[int, Field(ge=0, le=10_000)] = 0,
    ) -> TxnPage:
        """List transactions for a customer and/or merchant, newest first. Dates are inclusive."""
        if customer_id is None and merchant_id is None:
            raise InvalidRequestError("provide customer_id, merchant_id or both")
        if since and until and since > until:
            raise InvalidRequestError("since must be on or before until")
        if customer_id and store.get_customer(customer_id) is None:
            raise NotFoundError(f"customer {customer_id} not found")
        if merchant_id and store.get_merchant(merchant_id) is None:
            raise NotFoundError(f"merchant {merchant_id} not found")

        rows = store.list_transactions(
            customer_id=customer_id,
            merchant_id=merchant_id,
            since=_start_of(since) if since else None,
            until=_start_of(until + timedelta(days=1)) if until else None,
            limit=limit + 1,  # one extra row tells us whether there is another page
            offset=offset,
        )
        return TxnPage(items=rows[:limit], limit=limit, offset=offset, has_more=len(rows) > limit)

    @server.tool(annotations=READ_ONLY)
    def get_history(
        customer_id: CustomerIdParam,
        days: Annotated[int, Field(ge=1, le=730, description="Look-back window")] = 180,
    ) -> CustomerHistory:
        """Summarise a customer's spending and dispute history over the last N days."""
        if store.get_customer(customer_id) is None:
            raise NotFoundError(f"customer {customer_id} not found")
        end = clock()
        start = end - timedelta(days=days)
        txns = store.list_transactions(customer_id=customer_id, since=start, until=end)
        disputes = [
            c for c in store.list_cases(customer_id=customer_id) if start <= c.opened_at <= end
        ]
        counts = Counter(t.merchant_id for t in txns)
        totals: Counter[str] = Counter()
        for t in txns:
            totals[t.merchant_id] += t.amount_cents
        top = sorted(counts, key=lambda m: (-counts[m], m))[:5]
        return CustomerHistory(
            customer_id=customer_id,
            window_start=start,
            window_end=end,
            txn_count=len(txns),
            total_spend_cents=sum(t.amount_cents for t in txns),
            refunded_count=sum(t.status == TxnStatus.REFUNDED for t in txns),
            distinct_merchants=len(counts),
            top_merchants=[
                MerchantSpend(merchant_id=m, txn_count=counts[m], total_cents=totals[m])
                for m in top
            ],
            dispute_count=len(disputes),
            first_txn_at=min((t.posted_at for t in txns), default=None),
            last_txn_at=max((t.posted_at for t in txns), default=None),
        )

    return server


def _start_of(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)
