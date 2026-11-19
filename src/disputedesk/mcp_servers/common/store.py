"""Data access for the MCP servers.

Three narrow interfaces keep read-only and write access separate:
- `ReadStore`: lookups only. The transactions and merchant servers get nothing else.
- `CaseWriteStore`: the only two writes in M1 (append a draft, change a case's status).
- `AuditSink`: append an audit entry.

Stores are deliberately simple (fetch and filter); aggregation and business rules live in the
servers so `InMemoryStore` (unit tests) and `PostgresStore` (real use) cannot drift apart.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, LiteralString, Protocol

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from disputedesk.db.models import (
    AuditEntry,
    Case,
    CaseDraft,
    CaseStatus,
    Customer,
    Decision,
    Merchant,
    Shipment,
    Transaction,
)
from disputedesk.db.seed import SeedData
from disputedesk.mcp_servers.common.errors import ConflictError, NotFoundError


class ReadStore(Protocol):
    def get_customer(self, customer_id: str) -> Customer | None: ...
    def get_merchant(self, merchant_id: str) -> Merchant | None: ...
    def get_transaction(self, txn_id: str) -> Transaction | None: ...
    def list_transactions(
        self,
        *,
        customer_id: str | None = None,
        merchant_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Transaction]:
        """Newest first. `since` is inclusive, `until` exclusive."""
        ...

    def get_shipment(self, txn_id: str) -> Shipment | None: ...
    def get_case(self, case_id: str) -> Case | None: ...
    def list_cases(
        self, *, customer_id: str | None = None, merchant_id: str | None = None
    ) -> list[Case]: ...
    def list_drafts(self, case_id: str) -> list[CaseDraft]:
        """Oldest version first."""
        ...


class CaseWriteStore(Protocol):
    def add_draft(
        self,
        case_id: str,
        *,
        decision: Decision,
        rationale: str,
        letter: str,
        author: str,
        now: datetime,
    ) -> CaseDraft:
        """Append the next draft version for a case."""
        ...

    def set_case_status(
        self, case_id: str, *, expected: CaseStatus, new: CaseStatus, now: datetime
    ) -> Case:
        """Compare-and-set: raises ConflictError if the status is no longer `expected`."""
        ...


class AuditSink(Protocol):
    def write_audit(self, entry: AuditEntry) -> None: ...


# --------------------------------------------------------------------------- in-memory


class InMemoryStore:
    """Store backed by `SeedData`. Used by unit tests and anywhere a database is overkill."""

    def __init__(self, data: SeedData) -> None:
        self._lock = threading.Lock()
        self.customers = {c.customer_id: c for c in data.customers}
        self.merchants = {m.merchant_id: m for m in data.merchants}
        self.transactions = {t.txn_id: t for t in data.transactions}
        self.shipments = {s.txn_id: s for s in data.shipments}
        self.cases = {c.case_id: c for c in data.cases}
        self.drafts: dict[str, list[CaseDraft]] = {}
        self.audit: list[AuditEntry] = []

    def get_customer(self, customer_id: str) -> Customer | None:
        return self.customers.get(customer_id)

    def get_merchant(self, merchant_id: str) -> Merchant | None:
        return self.merchants.get(merchant_id)

    def get_transaction(self, txn_id: str) -> Transaction | None:
        return self.transactions.get(txn_id)

    def list_transactions(
        self,
        *,
        customer_id: str | None = None,
        merchant_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Transaction]:
        rows = [
            t
            for t in self.transactions.values()
            if (customer_id is None or t.customer_id == customer_id)
            and (merchant_id is None or t.merchant_id == merchant_id)
            and (since is None or t.posted_at >= since)
            and (until is None or t.posted_at < until)
        ]
        rows.sort(key=lambda t: (t.posted_at, t.txn_id), reverse=True)
        end = None if limit is None else offset + limit
        return rows[offset:end]

    def get_shipment(self, txn_id: str) -> Shipment | None:
        return self.shipments.get(txn_id)

    def get_case(self, case_id: str) -> Case | None:
        return self.cases.get(case_id)

    def list_cases(
        self, *, customer_id: str | None = None, merchant_id: str | None = None
    ) -> list[Case]:
        return sorted(
            (
                c
                for c in self.cases.values()
                if (customer_id is None or c.customer_id == customer_id)
                and (merchant_id is None or self.transactions[c.txn_id].merchant_id == merchant_id)
            ),
            key=lambda c: c.case_id,
        )

    def list_drafts(self, case_id: str) -> list[CaseDraft]:
        return list(self.drafts.get(case_id, []))

    def add_draft(
        self,
        case_id: str,
        *,
        decision: Decision,
        rationale: str,
        letter: str,
        author: str,
        now: datetime,
    ) -> CaseDraft:
        with self._lock:
            if case_id not in self.cases:
                raise NotFoundError(f"case {case_id} not found")
            drafts = self.drafts.setdefault(case_id, [])
            draft = CaseDraft(
                case_id=case_id,
                version=len(drafts) + 1,
                decision=decision,
                rationale=rationale,
                letter=letter,
                author=author,
                created_at=now,
            )
            drafts.append(draft)
            return draft

    def set_case_status(
        self, case_id: str, *, expected: CaseStatus, new: CaseStatus, now: datetime
    ) -> Case:
        with self._lock:
            case = self.cases.get(case_id)
            if case is None:
                raise NotFoundError(f"case {case_id} not found")
            if case.status != expected:
                raise ConflictError(f"case {case_id} status changed concurrently; retry")
            updated = case.model_copy(update={"status": new, "updated_at": now})
            self.cases[case_id] = updated
            return updated

    def write_audit(self, entry: AuditEntry) -> None:
        with self._lock:
            self.audit.append(entry)


# --------------------------------------------------------------------------- Postgres


class PostgresStore:
    """Store backed by the app database (schema in disputedesk/db/schema.sql).

    One autocommit connection per server process; psycopg serialises use across threads.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def _one(self, query: LiteralString, params: tuple[Any, ...]) -> dict[str, Any] | None:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return cur.fetchone()

    def _all(
        self, query: LiteralString, params: tuple[Any, ...] | dict[str, Any]
    ) -> list[dict[str, Any]]:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return cur.fetchall()

    def get_customer(self, customer_id: str) -> Customer | None:
        row = self._one("SELECT * FROM customers WHERE customer_id = %s", (customer_id,))
        return Customer.model_validate(row) if row else None

    def get_merchant(self, merchant_id: str) -> Merchant | None:
        row = self._one("SELECT * FROM merchants WHERE merchant_id = %s", (merchant_id,))
        return Merchant.model_validate(row) if row else None

    def get_transaction(self, txn_id: str) -> Transaction | None:
        row = self._one("SELECT * FROM transactions WHERE txn_id = %s", (txn_id,))
        return Transaction.model_validate(row) if row else None

    def list_transactions(
        self,
        *,
        customer_id: str | None = None,
        merchant_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Transaction]:
        # NULL parameters disable their filter, so the SQL text stays static.
        rows = self._all(
            """
            SELECT * FROM transactions
            WHERE (%(customer_id)s::text IS NULL OR customer_id = %(customer_id)s)
              AND (%(merchant_id)s::text IS NULL OR merchant_id = %(merchant_id)s)
              AND (%(since)s::timestamptz IS NULL OR posted_at >= %(since)s)
              AND (%(until)s::timestamptz IS NULL OR posted_at < %(until)s)
            ORDER BY posted_at DESC, txn_id DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {
                "customer_id": customer_id,
                "merchant_id": merchant_id,
                "since": since,
                "until": until,
                "limit": limit,
                "offset": offset,
            },
        )
        return [Transaction.model_validate(r) for r in rows]

    def get_shipment(self, txn_id: str) -> Shipment | None:
        row = self._one("SELECT * FROM shipments WHERE txn_id = %s", (txn_id,))
        return Shipment.model_validate(row) if row else None

    def get_case(self, case_id: str) -> Case | None:
        row = self._one("SELECT * FROM cases WHERE case_id = %s", (case_id,))
        return Case.model_validate(row) if row else None

    def list_cases(
        self, *, customer_id: str | None = None, merchant_id: str | None = None
    ) -> list[Case]:
        rows = self._all(
            """
            SELECT c.* FROM cases c JOIN transactions t USING (txn_id)
            WHERE (%s::text IS NULL OR c.customer_id = %s)
              AND (%s::text IS NULL OR t.merchant_id = %s)
            ORDER BY c.case_id
            """,
            (customer_id, customer_id, merchant_id, merchant_id),
        )
        return [Case.model_validate(r) for r in rows]

    def list_drafts(self, case_id: str) -> list[CaseDraft]:
        rows = self._all(
            "SELECT * FROM case_drafts WHERE case_id = %s ORDER BY version", (case_id,)
        )
        return [CaseDraft.model_validate(r) for r in rows]

    def add_draft(
        self,
        case_id: str,
        *,
        decision: Decision,
        rationale: str,
        letter: str,
        author: str,
        now: datetime,
    ) -> CaseDraft:
        with self._conn.transaction(), self._conn.cursor(row_factory=dict_row) as cur:
            # Lock the case row so concurrent drafts get distinct version numbers.
            cur.execute("SELECT 1 FROM cases WHERE case_id = %s FOR UPDATE", (case_id,))
            if cur.fetchone() is None:
                raise NotFoundError(f"case {case_id} not found")
            cur.execute(
                """
                INSERT INTO case_drafts
                    (case_id, version, decision, rationale, letter, author, created_at)
                SELECT %s, COALESCE(MAX(version), 0) + 1, %s, %s, %s, %s, %s
                FROM case_drafts WHERE case_id = %s
                RETURNING *
                """,
                (case_id, decision, rationale, letter, author, now, case_id),
            )
            row = cur.fetchone()
        assert row is not None
        return CaseDraft.model_validate(row)

    def set_case_status(
        self, case_id: str, *, expected: CaseStatus, new: CaseStatus, now: datetime
    ) -> Case:
        row = self._one(
            """
            UPDATE cases SET status = %s, updated_at = %s
            WHERE case_id = %s AND status = %s
            RETURNING *
            """,
            (new, now, case_id, expected),
        )
        if row is None:
            if self.get_case(case_id) is None:
                raise NotFoundError(f"case {case_id} not found")
            raise ConflictError(f"case {case_id} status changed concurrently; retry")
        return Case.model_validate(row)

    def write_audit(self, entry: AuditEntry) -> None:
        self._conn.execute(
            """
            INSERT INTO audit_log
                (created_at, actor, server, tool, case_id, arguments, outcome, error, duration_ms)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                entry.created_at,
                entry.actor,
                entry.server,
                entry.tool,
                entry.case_id,
                Jsonb(entry.arguments),
                entry.outcome,
                entry.error,
                entry.duration_ms,
            ),
        )
