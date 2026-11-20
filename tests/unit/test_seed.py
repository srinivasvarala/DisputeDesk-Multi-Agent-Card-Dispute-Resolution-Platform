from collections import Counter

from disputedesk.db.models import ReasonCode, ShipmentStatus, TxnStatus
from disputedesk.db.seed import DISPUTE_MIX, REFERENCE_TIME, generate


def test_counts_match_spec() -> None:
    data = generate()
    assert len(data.customers) == 50
    assert len(data.merchants) == 20
    assert len(data.transactions) == 200
    assert len(data.cases) == 40
    assert Counter(c.reason_code for c in data.cases) == DISPUTE_MIX


def test_same_seed_same_data_and_different_seed_differs() -> None:
    assert generate(42) == generate(42)
    assert generate(42).transactions != generate(7).transactions


def test_ids_unique_and_references_valid() -> None:
    data = generate()
    customers = {c.customer_id for c in data.customers}
    merchants = {m.merchant_id: m for m in data.merchants}
    txns = {t.txn_id: t for t in data.transactions}
    assert len(customers) == len(data.customers)
    assert len(txns) == len(data.transactions)

    for t in data.transactions:
        assert t.customer_id in customers
        assert t.merchant_id in merchants
        assert t.posted_at < REFERENCE_TIME
    for s in data.shipments:
        assert merchants[txns[s.txn_id].merchant_id].ships_physical_goods
    # At most one dispute per transaction; dispute matches its transaction.
    assert len({c.txn_id for c in data.cases}) == len(data.cases)
    for c in data.cases:
        txn = txns[c.txn_id]
        assert c.customer_id == txn.customer_id
        assert c.amount_cents == txn.amount_cents
        assert txn.status == TxnStatus.SETTLED
        assert txn.posted_at <= c.opened_at <= REFERENCE_TIME


def test_disputes_have_supporting_evidence_shape() -> None:
    data = generate()
    txns = {t.txn_id: t for t in data.transactions}
    shipments = {s.txn_id: s for s in data.shipments}
    for c in data.cases:
        if c.reason_code in (ReasonCode.NOT_RECEIVED, ReasonCode.NOT_AS_DESCRIBED):
            assert c.txn_id in shipments, "goods disputes need delivery data"
        if c.reason_code == ReasonCode.DUPLICATE:
            dupe = txns[c.txn_id]
            twins = [
                t
                for t in data.transactions
                if t.txn_id != dupe.txn_id
                and (t.customer_id, t.merchant_id, t.amount_cents)
                == (dupe.customer_id, dupe.merchant_id, dupe.amount_cents)
            ]
            assert twins, "duplicate dispute must have an identical earlier charge"
    # A duplicate charge is not a second order: no shipment of its own.
    for c in data.cases:
        if c.reason_code == ReasonCode.DUPLICATE:
            assert c.txn_id not in shipments
    # "Not as described" means the item arrived.
    for c in data.cases:
        if c.reason_code == ReasonCode.NOT_AS_DESCRIBED:
            assert shipments[c.txn_id].status == ShipmentStatus.DELIVERED
    # Some "not received" claims are contradicted by a delivered shipment (reject cases).
    nr_status = {shipments[c.txn_id].status for c in data.cases if c.reason_code == "not_received"}
    assert ShipmentStatus.DELIVERED in nr_status
    assert nr_status - {ShipmentStatus.DELIVERED}


def test_all_contact_data_is_synthetic() -> None:
    for c in generate().customers:
        assert c.email.endswith("@example.com")


def test_new_facts_are_derived_without_changing_the_rest_of_the_seed() -> None:
    from disputedesk.db.models import Channel, EntryMode

    data = generate()
    card_present = [t for t in data.transactions if t.channel == Channel.CARD_PRESENT]
    assert card_present
    assert all(t.entry_mode == EntryMode.CHIP_PIN for t in card_present)
    assert all(t.entry_mode is None for t in data.transactions if t.channel == Channel.ONLINE)
    delivered = [s for s in data.shipments if s.status == ShipmentStatus.DELIVERED]
    assert all(s.delivery_address_matches is True for s in delivered)
    assert all(s.delivery_address_matches is None for s in data.shipments if s not in delivered)
    fraud = [c for c in data.cases if c.reason_code == ReasonCode.FRAUD]
    assert all(c.fraud_declaration_signed and c.card_reissued for c in fraud)
    assert all(c.fraud_declaration_signed is None for c in data.cases if c not in fraud)
