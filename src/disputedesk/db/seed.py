"""Deterministic synthetic seed data: 50 customers, 20 merchants, 200 transactions, 40 disputes.

Everything here is fake. `generate()` is pure (same seed -> same data) so unit tests and the
in-memory store can use it without a database. The CLI loads it into Postgres:

    uv run python -m disputedesk.db.seed           # load into an empty database
    uv run python -m disputedesk.db.seed --reset   # wipe app data (not audit_log) and reload
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import psycopg

from disputedesk.db import apply_schema, connect
from disputedesk.db.models import (
    Case,
    CaseStatus,
    Channel,
    Customer,
    Merchant,
    MerchantCategory,
    ReasonCode,
    RiskTier,
    Shipment,
    ShipmentStatus,
    Transaction,
    TxnStatus,
)

DEFAULT_SEED = 42
# Fixed anchor so the data never depends on when the seed runs.
REFERENCE_TIME = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)

N_CUSTOMERS = 50
N_MERCHANTS = 20
N_TRANSACTIONS = 200
DISPUTE_MIX: dict[ReasonCode, int] = {
    ReasonCode.FRAUD: 12,
    ReasonCode.NOT_RECEIVED: 10,
    ReasonCode.DUPLICATE: 8,
    ReasonCode.NOT_AS_DESCRIBED: 10,
}
N_DISPUTES = sum(DISPUTE_MIX.values())

_FIRST_NAMES = [
    "Avery", "Blake", "Casey", "Devon", "Emerson", "Finley", "Harper", "Jordan", "Kendall",
    "Logan", "Morgan", "Parker", "Quinn", "Reese", "Riley", "Rowan", "Sage", "Skyler", "Taylor",
    "Wren",
]  # fmt: skip
_LAST_NAMES = [
    "Ashford", "Brightwater", "Calloway", "Dunmore", "Ellery", "Fairbanks", "Greystone",
    "Hollins", "Ironwood", "Juniper", "Kestrel", "Lockridge", "Marlowe", "Northcott", "Oakhurst",
]  # fmt: skip

# (name, category, mcc, ships_physical_goods, risk_tier). All fictional.
_MERCHANTS: list[tuple[str, MerchantCategory, str, bool, RiskTier]] = [
    ("Bluefin Gadgets", MerchantCategory.ELECTRONICS, "5732", True, RiskTier.HIGH),
    ("Voltline Electronics", MerchantCategory.ELECTRONICS, "5732", True, RiskTier.LOW),
    ("Copperleaf Apparel", MerchantCategory.APPAREL, "5651", True, RiskTier.MEDIUM),
    ("Threadbare Outlet", MerchantCategory.APPAREL, "5651", True, RiskTier.HIGH),
    ("Juniper & Pine Home", MerchantCategory.HOME_GOODS, "5712", True, RiskTier.LOW),
    ("Hearthstone Living", MerchantCategory.HOME_GOODS, "5712", True, RiskTier.MEDIUM),
    ("Quickcart Deals", MerchantCategory.ELECTRONICS, "5732", True, RiskTier.HIGH),
    ("Meadowlark Books & Gifts", MerchantCategory.HOME_GOODS, "5942", True, RiskTier.LOW),
    ("Tidewater Travel Co", MerchantCategory.TRAVEL, "4722", False, RiskTier.MEDIUM),
    ("Skylark Airways", MerchantCategory.TRAVEL, "4511", False, RiskTier.LOW),
    ("Lumen Streaming", MerchantCategory.STREAMING, "4899", False, RiskTier.LOW),
    ("Nightjar Media", MerchantCategory.STREAMING, "4899", False, RiskTier.MEDIUM),
    ("Cobalt Cloud Tools", MerchantCategory.SOFTWARE, "5734", False, RiskTier.LOW),
    ("Pixelforge Apps", MerchantCategory.SOFTWARE, "5734", False, RiskTier.MEDIUM),
    ("Saffron Table", MerchantCategory.RESTAURANT, "5812", False, RiskTier.LOW),
    ("Ember & Oak Grill", MerchantCategory.RESTAURANT, "5812", False, RiskTier.LOW),
    ("Harvest Lane Market", MerchantCategory.GROCERY, "5411", False, RiskTier.LOW),
    ("Greenbasket Grocers", MerchantCategory.GROCERY, "5411", False, RiskTier.LOW),
    ("Fernway Hotels", MerchantCategory.TRAVEL, "7011", False, RiskTier.LOW),
    ("Orbit Gear Online", MerchantCategory.APPAREL, "5651", True, RiskTier.MEDIUM),
]
assert len(_MERCHANTS) == N_MERCHANTS

# Amount range in cents per category.
_AMOUNTS: dict[MerchantCategory, tuple[int, int]] = {
    MerchantCategory.ELECTRONICS: (2_500, 180_000),
    MerchantCategory.APPAREL: (1_500, 25_000),
    MerchantCategory.HOME_GOODS: (1_000, 60_000),
    MerchantCategory.TRAVEL: (8_000, 250_000),
    MerchantCategory.STREAMING: (599, 2_499),
    MerchantCategory.SOFTWARE: (999, 30_000),
    MerchantCategory.RESTAURANT: (1_200, 18_000),
    MerchantCategory.GROCERY: (800, 30_000),
}
# Riskier merchants get more traffic and attract more disputes.
_TRAFFIC_WEIGHT = {RiskTier.LOW: 1.0, RiskTier.MEDIUM: 1.3, RiskTier.HIGH: 1.8}
_DISPUTE_WEIGHT = {RiskTier.LOW: 1.0, RiskTier.MEDIUM: 2.0, RiskTier.HIGH: 5.0}

_CARRIERS = ["ParcelPath", "SwiftShip", "Northstar Freight"]

_NARRATIVES: dict[ReasonCode, list[str]] = {
    ReasonCode.FRAUD: [
        "I don't recognise this charge from {merchant}. I have never shopped there.",
        "My card was in my wallet the whole time. I did not make this {amount} purchase.",
        "Someone used my card at {merchant} without permission.",
    ],
    ReasonCode.NOT_RECEIVED: [
        "I ordered from {merchant} weeks ago and the package never arrived.",
        "Tracking stopped updating and {merchant} won't answer my emails. Nothing was delivered.",
        "I paid {amount} but never received my order.",
    ],
    ReasonCode.DUPLICATE: [
        "{merchant} charged me twice for the same order of {amount}.",
        "I see two identical charges from {merchant}. I only bought one item.",
    ],
    ReasonCode.NOT_AS_DESCRIBED: [
        "The item from {merchant} looks used and is the wrong size, nothing like the listing.",
        "What arrived from {merchant} is a cheap imitation, not what was advertised.",
        "The product stopped working on day one and {merchant} refuses a return.",
    ],
}


@dataclass(frozen=True)
class SeedData:
    customers: list[Customer]
    merchants: list[Merchant]
    transactions: list[Transaction]
    shipments: list[Shipment]
    cases: list[Case]


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _customers(rng: random.Random) -> list[Customer]:
    out: list[Customer] = []
    for i in range(1, N_CUSTOMERS + 1):
        first, last = rng.choice(_FIRST_NAMES), rng.choice(_LAST_NAMES)
        out.append(
            Customer(
                customer_id=f"cus_{i:04d}",
                name=f"{first} {last}",
                email=f"{first.lower()}.{last.lower()}{i}@example.com",
                card_last4=f"{rng.randint(0, 9999):04d}",
                created_at=REFERENCE_TIME - timedelta(days=rng.randint(200, 2000)),
            )
        )
    return out


def _merchants(rng: random.Random) -> list[Merchant]:
    return [
        Merchant(
            merchant_id=f"mer_{i:03d}",
            name=name,
            category=category,
            mcc=mcc,
            country="US",
            ships_physical_goods=physical,
            risk_tier=tier,
            onboarded_at=REFERENCE_TIME - timedelta(days=rng.randint(300, 3000)),
        )
        for i, (name, category, mcc, physical, tier) in enumerate(_MERCHANTS, start=1)
    ]


@dataclass
class _TxnDraft:
    customer_id: str
    merchant_id: str
    amount_cents: int
    channel: Channel
    status: TxnStatus
    description: str
    posted_at: datetime
    duplicate_of: _TxnDraft | None = None


def _transactions(
    rng: random.Random, customers: list[Customer], merchants: list[Merchant]
) -> tuple[list[Transaction], list[tuple[Transaction, Transaction]]]:
    """Return all transactions plus the (original, duplicate) pairs used for duplicate disputes."""
    n_dupes = DISPUTE_MIX[ReasonCode.DUPLICATE]
    weights = [_TRAFFIC_WEIGHT[m.risk_tier] for m in merchants]
    base: list[_TxnDraft] = []
    for _ in range(N_TRANSACTIONS - n_dupes):
        merchant = rng.choices(merchants, weights=weights)[0]
        low, high = _AMOUNTS[merchant.category]
        in_person = merchant.category in (MerchantCategory.RESTAURANT, MerchantCategory.GROCERY)
        base.append(
            _TxnDraft(
                customer_id=rng.choice(customers).customer_id,
                merchant_id=merchant.merchant_id,
                amount_cents=rng.randint(low, high),
                channel=Channel.CARD_PRESENT if in_person else Channel.ONLINE,
                # Refunded transactions are never disputed (see _cases).
                status=TxnStatus.REFUNDED if rng.random() < 0.05 else TxnStatus.SETTLED,
                description=f"{merchant.name} purchase",
                posted_at=REFERENCE_TIME
                - timedelta(days=rng.randint(10, 180), minutes=rng.randint(0, 1439)),
            )
        )
    # Duplicate charges: same customer, merchant and amount a few minutes apart.
    settled = [d for d in base if d.status == TxnStatus.SETTLED]
    dupes = [
        replace(
            src, posted_at=src.posted_at + timedelta(minutes=rng.randint(1, 15)), duplicate_of=src
        )
        for src in rng.sample(settled, n_dupes)
    ]
    drafts = sorted(base + dupes, key=lambda d: d.posted_at)

    txns: list[Transaction] = []
    by_draft: dict[int, Transaction] = {}
    for n, d in enumerate(drafts, start=1):
        txn = Transaction(
            txn_id=f"txn_{n:06d}",
            customer_id=d.customer_id,
            merchant_id=d.merchant_id,
            amount_cents=d.amount_cents,
            channel=d.channel,
            status=d.status,
            description=d.description,
            posted_at=d.posted_at,
        )
        txns.append(txn)
        by_draft[id(d)] = txn
    pairs = [(by_draft[id(d.duplicate_of)], by_draft[id(d)]) for d in dupes if d.duplicate_of]
    return txns, pairs


def _shipments(
    rng: random.Random,
    txns: list[Transaction],
    merchants: dict[str, Merchant],
    duplicate_charges: set[str],
) -> list[Shipment]:
    """One shipment per physical-goods order. A duplicate charge is not a second order, so it
    gets no shipment of its own."""
    out: list[Shipment] = []
    for txn in txns:
        if not merchants[txn.merchant_id].ships_physical_goods or txn.txn_id in duplicate_charges:
            continue
        shipped = txn.posted_at + timedelta(days=rng.randint(1, 3))
        roll = rng.random()
        if roll < 0.80:
            status, delivered = (
                ShipmentStatus.DELIVERED,
                shipped + timedelta(days=rng.randint(2, 7)),
            )
        elif roll < 0.90:
            status, delivered = ShipmentStatus.IN_TRANSIT, None
        elif roll < 0.96:
            status, delivered = ShipmentStatus.LOST, None
        else:
            status, delivered = ShipmentStatus.RETURNED, None
        out.append(
            Shipment(
                shipment_id=f"shp_{len(out) + 1:06d}",
                txn_id=txn.txn_id,
                carrier=rng.choice(_CARRIERS),
                tracking_number=f"TRK{rng.randint(10**9, 10**10 - 1)}",
                status=status,
                shipped_at=shipped,
                delivered_at=delivered,
            )
        )
    return out


def _cases(
    rng: random.Random,
    txns: list[Transaction],
    dupe_pairs: list[tuple[Transaction, Transaction]],
    merchants: dict[str, Merchant],
    shipments: dict[str, Shipment],
) -> list[Case]:
    used: set[str] = set()
    picks: list[tuple[ReasonCode, Transaction]] = []

    # Duplicate disputes point at the second (duplicate) charge.
    for _, dupe in dupe_pairs:
        picks.append((ReasonCode.DUPLICATE, dupe))
        used.add(dupe.txn_id)
    dupe_ids = {d.txn_id for _, d in dupe_pairs} | {o.txn_id for o, _ in dupe_pairs}

    def pool(reason: ReasonCode) -> tuple[list[Transaction], list[float]]:
        physical_only = reason != ReasonCode.FRAUD
        # "Not as described" means the item arrived, so it needs a delivered shipment.
        delivered_only = reason == ReasonCode.NOT_AS_DESCRIBED
        undelivered_bias = reason == ReasonCode.NOT_RECEIVED
        candidates = [
            t
            for t in txns
            if t.status == TxnStatus.SETTLED
            and t.txn_id not in used
            and t.txn_id not in dupe_ids
            and (not physical_only or t.txn_id in shipments)
            and (not delivered_only or shipments[t.txn_id].status == ShipmentStatus.DELIVERED)
        ]
        weights = []
        for t in candidates:
            w = _DISPUTE_WEIGHT[merchants[t.merchant_id].risk_tier]
            if undelivered_bias and shipments[t.txn_id].status != ShipmentStatus.DELIVERED:
                w *= 6  # most "not received" claims are genuine; a few are not
            weights.append(w)
        return candidates, weights

    for reason in (ReasonCode.NOT_RECEIVED, ReasonCode.NOT_AS_DESCRIBED, ReasonCode.FRAUD):
        for _ in range(DISPUTE_MIX[reason]):
            candidates, weights = pool(reason)
            txn = rng.choices(candidates, weights=weights)[0]
            picks.append((reason, txn))
            used.add(txn.txn_id)

    picks.sort(key=lambda p: p[1].posted_at)
    out: list[Case] = []
    for n, (reason, txn) in enumerate(picks, start=1):
        merchant = merchants[txn.merchant_id]
        opened = min(txn.posted_at + timedelta(days=rng.randint(3, 40)), REFERENCE_TIME)
        status = CaseStatus.IN_REVIEW if rng.random() < 0.15 else CaseStatus.OPEN
        narrative = rng.choice(_NARRATIVES[reason]).format(
            merchant=merchant.name, amount=_money(txn.amount_cents)
        )
        out.append(
            Case(
                case_id=f"case_{n:04d}",
                txn_id=txn.txn_id,
                customer_id=txn.customer_id,
                reason_code=reason,
                amount_cents=txn.amount_cents,
                customer_narrative=narrative,
                status=status,
                opened_at=opened,
                updated_at=opened,
            )
        )
    return out


def generate(seed: int = DEFAULT_SEED) -> SeedData:
    """Build the full synthetic dataset. Pure and deterministic for a given seed."""
    rng = random.Random(seed)
    customers = _customers(rng)
    merchants = _merchants(rng)
    txns, dupe_pairs = _transactions(rng, customers, merchants)
    by_merchant = {m.merchant_id: m for m in merchants}
    shipments = _shipments(rng, txns, by_merchant, {dupe.txn_id for _, dupe in dupe_pairs})
    cases = _cases(rng, txns, dupe_pairs, by_merchant, {s.txn_id: s for s in shipments})
    return SeedData(customers, merchants, txns, shipments, cases)


# ---------------------------------------------------------------- Postgres loading

_APP_TABLES = "case_drafts, cases, shipments, transactions, merchants, customers"


def load(conn: psycopg.Connection, data: SeedData, *, reset: bool = False) -> None:
    """Insert seed data in one transaction. audit_log is never touched."""
    apply_schema(conn)
    with conn.transaction(), conn.cursor() as cur:
        if reset:
            cur.execute(f"TRUNCATE {_APP_TABLES}")
        else:
            cur.execute("SELECT EXISTS (SELECT 1 FROM customers)")
            row = cur.fetchone()
            if row and row[0]:
                raise RuntimeError("database already seeded; rerun with --reset to reload")
        cur.executemany(
            "INSERT INTO customers VALUES (%s, %s, %s, %s, %s)",
            [(c.customer_id, c.name, c.email, c.card_last4, c.created_at) for c in data.customers],
        )
        cur.executemany(
            "INSERT INTO merchants VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (m.merchant_id, m.name, m.category, m.mcc, m.country, m.ships_physical_goods,
                 m.risk_tier, m.onboarded_at)
                for m in data.merchants
            ],
        )  # fmt: skip
        cur.executemany(
            "INSERT INTO transactions VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (t.txn_id, t.customer_id, t.merchant_id, t.amount_cents, t.currency, t.channel,
                 t.status, t.description, t.posted_at)
                for t in data.transactions
            ],
        )  # fmt: skip
        cur.executemany(
            "INSERT INTO shipments VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [
                (s.shipment_id, s.txn_id, s.carrier, s.tracking_number, s.status, s.shipped_at,
                 s.delivered_at)
                for s in data.shipments
            ],
        )  # fmt: skip
        cur.executemany(
            "INSERT INTO cases VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (c.case_id, c.txn_id, c.customer_id, c.reason_code, c.amount_cents,
                 c.customer_narrative, c.status, c.opened_at, c.updated_at)
                for c in data.cases
            ],
        )  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load synthetic DisputeDesk data into Postgres.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="random seed")
    parser.add_argument("--reset", action="store_true", help="wipe app tables first")
    args = parser.parse_args(argv)

    data = generate(args.seed)
    with connect() as conn:
        try:
            load(conn, data, reset=args.reset)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    print(
        f"seeded {len(data.customers)} customers, {len(data.merchants)} merchants, "
        f"{len(data.transactions)} transactions, {len(data.shipments)} shipments, "
        f"{len(data.cases)} disputes (seed={args.seed})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
