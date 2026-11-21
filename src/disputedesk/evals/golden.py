"""The golden dataset: schema, loader, and the in-memory world each case is run in.

Every row is a self-contained synthetic dispute (customer, merchant, charge, shipment, narrative)
with the labels the system is scored against. Timestamps are stored as "days ago" so the data
never goes stale: `build_world` anchors them to the `now` it is given.

Adversarial rows carry an `attack` kind, the clean case they twist (`twin`), the decisions that
count as "not manipulated" (`acceptable_decisions`), strings that must never appear in the
letter, and what the input guardrail is expected to do with the attack text.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from disputedesk.db.models import (
    Case,
    CaseStatus,
    Channel,
    Customer,
    Decision,
    EntryMode,
    Merchant,
    MerchantCategory,
    ReasonCode,
    RiskTier,
    Shipment,
    ShipmentStatus,
    Transaction,
    TxnStatus,
)
from disputedesk.db.seed import SeedData
from disputedesk.mcp_servers.common.store import InMemoryStore
from disputedesk.rag.evaluation import Target

DEFAULT_GOLDEN = Path("evals/datasets/golden.jsonl")
FILLER_CUSTOMER = "cus_9999"
# Undisputed charges per merchant, so the dispute rate the agents see matches the risk tier.
_FILLERS = {RiskTier.LOW: 300, RiskTier.MEDIUM: 100, RiskTier.HIGH: 40}


class Attack(StrEnum):
    NONE = "none"
    IGNORE_INSTRUCTIONS = "ignore_instructions"
    TAG_BREAKOUT = "tag_breakout"
    FAKE_AUTHORITY = "fake_authority"
    JAILBREAK = "jailbreak"
    FAKE_POLICY = "fake_policy"
    AMOUNT_TAMPERING = "amount_tampering"
    PII_BAIT = "pii_bait"
    PROMPT_EXFIL = "prompt_exfil"
    TOOL_DATA_INJECTION = "tool_data_injection"
    APPROVAL_BYPASS = "approval_bypass"


class GuardrailExpect(StrEnum):
    ALLOW = "allow"
    REDACT = "redact"
    BLOCK = "block"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GoldenMerchant(Model):
    name: str
    category: MerchantCategory
    mcc: str = Field(pattern=r"^\d{4}$")
    ships_physical_goods: bool = True
    risk_tier: RiskTier = RiskTier.LOW


class GoldenShipment(Model):
    carrier: str
    status: ShipmentStatus
    tracking_number: str
    shipped_days_ago: int
    delivered_days_ago: int | None = None
    delivery_address_matches: bool | None = None
    signature_confirmed: bool | None = None


class EarlierCharge(Model):
    """Another charge by the same customer at the same merchant, before the disputed one."""

    hours_before: int = Field(gt=0)
    status: TxnStatus = TxnStatus.SETTLED
    amount_cents: int | None = Field(default=None, gt=0, description="None = same amount")


class Expected(Model):
    reason_code: ReasonCode
    decision: Decision
    acceptable_decisions: list[Decision] = Field(
        default_factory=list, description="Also fine besides `decision`; empty = just that one"
    )
    approval_required: bool
    citations: list[Target] = Field(min_length=1)

    @property
    def decisions(self) -> set[Decision]:
        return set(self.acceptable_decisions) | {self.decision}


class GoldenCase(Model):
    id: str = Field(pattern=r"^[ga]\d{2}$")
    kind: Literal["normal", "adversarial"]
    attack: Attack = Attack.NONE
    twin: str | None = None
    customer_name: str
    card_last4: str = Field(pattern=r"^\d{4}$")
    merchant: GoldenMerchant
    amount_cents: int = Field(gt=0)
    channel: Channel = Channel.ONLINE
    txn_status: TxnStatus = TxnStatus.SETTLED
    description: str
    days_ago: int = 40
    shipment: GoldenShipment | None = None
    earlier_charges: list[EarlierCharge] = Field(default_factory=list)
    prior_disputes: int = Field(default=0, ge=0, description="Other disputes in the last 90 days")
    entry_mode: EntryMode | None = None
    fraud_declaration_signed: bool | None = None
    card_reissued: bool | None = None
    card_reported_lost_hours_before_txn: float | None = Field(
        default=None, description="None = the card was not reported lost or stolen"
    )
    # Not-as-described filing items, as the case record would hold them. None = not recorded.
    merchant_contacted_days_before_filing: int | None = Field(default=None, ge=0)
    return_tracking_provided: bool | None = None
    merchant_refused_return: bool | None = None
    claimed_reason: ReasonCode
    narrative: str
    expected: Expected
    forbidden_in_letter: list[str] = Field(
        default_factory=list, description="Strings that must never appear in the letter at all"
    )
    attacker_demand: str | None = Field(
        default=None,
        description=(
            "What the attacker demanded or asserted (an amount, a fake rule). Quoting it while "
            "refusing is fine; the letter must not GRANT or APPLY it (a judge decides, see "
            "evals/demand_judge.py)"
        ),
    )
    guardrail: GuardrailExpect = GuardrailExpect.ALLOW
    guardrail_input: str | None = Field(
        default=None, description="Text for the input guardrail; None = the narrative"
    )

    @model_validator(mode="after")
    def _kind_matches_attack(self) -> GoldenCase:
        if (self.kind == "adversarial") != (self.attack != Attack.NONE):
            raise ValueError(f"{self.id}: kind and attack disagree")
        return self

    @property
    def text_for_guardrail(self) -> str:
        return self.guardrail_input or self.narrative


def load_golden(path: Path = DEFAULT_GOLDEN) -> list[GoldenCase]:
    cases = [
        GoldenCase.model_validate_json(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate golden case ids")
    known = set(ids)
    for c in cases:
        if c.twin is not None and c.twin not in known:
            raise ValueError(f"{c.id}: twin {c.twin} does not exist")
    return cases


def _charge(
    c: GoldenCase,
    customer_id: str,
    merchant_id: str,
    txn_id: str,
    when: datetime,
    *,
    status: TxnStatus,
    cents: int,
    entry_mode: EntryMode | None = None,
) -> Transaction:
    return Transaction(
        txn_id=txn_id,
        customer_id=customer_id,
        merchant_id=merchant_id,
        amount_cents=cents,
        channel=c.channel,
        status=status,
        description=c.description,
        posted_at=when,
        entry_mode=entry_mode,
    )


class World:
    """The in-memory store holding every golden case, and each case's id in it."""

    def __init__(self, store: InMemoryStore, case_ids: dict[str, str]) -> None:
        self.store = store
        self.case_ids = case_ids


def build_world(cases: list[GoldenCase], now: datetime) -> World:
    customers: list[Customer] = []
    merchants: list[Merchant] = []
    txns: list[Transaction] = []
    shipments: list[Shipment] = []
    disputes: list[Case] = []
    case_ids: dict[str, str] = {}
    filler_n = 100_000
    extra_n = 200_000

    customers.append(
        Customer(
            customer_id=FILLER_CUSTOMER,
            name="Filler Customer",
            email="filler@example.com",
            card_last4="0000",
            created_at=now - timedelta(days=900),
        )
    )
    for i, c in enumerate(cases, start=1):
        customer_id, merchant_id = f"cus_{i:04d}", f"mer_{i:03d}"
        txn_id, case_id = f"txn_{i * 10:06d}", f"case_{i:04d}"
        posted = now - timedelta(days=c.days_ago)
        case_ids[c.id] = case_id
        customers.append(
            Customer(
                customer_id=customer_id,
                name=c.customer_name,
                email=f"golden{i}@example.com",
                card_last4=c.card_last4,
                created_at=now - timedelta(days=700),
            )
        )
        merchants.append(
            Merchant(
                merchant_id=merchant_id,
                name=c.merchant.name,
                category=c.merchant.category,
                mcc=c.merchant.mcc,
                country="US",
                ships_physical_goods=c.merchant.ships_physical_goods,
                risk_tier=c.merchant.risk_tier,
                onboarded_at=now - timedelta(days=800),
            )
        )

        txns.append(
            _charge(
                c,
                customer_id,
                merchant_id,
                txn_id,
                posted,
                status=c.txn_status,
                cents=c.amount_cents,
                entry_mode=c.entry_mode,
            )
        )
        for k, e in enumerate(c.earlier_charges, start=1):
            txns.append(
                _charge(
                    c,
                    customer_id,
                    merchant_id,
                    f"txn_{i * 10 + k:06d}",
                    posted - timedelta(hours=e.hours_before),
                    status=e.status,
                    cents=e.amount_cents or c.amount_cents,
                )
            )
        if c.shipment is not None:
            s = c.shipment
            shipments.append(
                Shipment(
                    shipment_id=f"shp_{i:06d}",
                    txn_id=txn_id,
                    carrier=s.carrier,
                    tracking_number=s.tracking_number,
                    status=s.status,
                    shipped_at=now - timedelta(days=s.shipped_days_ago),
                    delivered_at=(
                        now - timedelta(days=s.delivered_days_ago)
                        if s.delivered_days_ago is not None
                        else None
                    ),
                    delivery_address_matches=s.delivery_address_matches,
                    signature_confirmed=s.signature_confirmed,
                )
            )
        disputes.append(
            Case(
                case_id=case_id,
                txn_id=txn_id,
                customer_id=customer_id,
                reason_code=c.claimed_reason,
                amount_cents=c.amount_cents,
                customer_narrative=c.narrative,
                status=CaseStatus.OPEN,
                opened_at=now - timedelta(days=1),
                updated_at=now - timedelta(days=1),
                fraud_declaration_signed=c.fraud_declaration_signed,
                card_reissued=c.card_reissued,
                card_reported_lost_at=(
                    posted - timedelta(hours=c.card_reported_lost_hours_before_txn)
                    if c.card_reported_lost_hours_before_txn is not None
                    else None
                ),
                merchant_contacted_at=(
                    now - timedelta(days=1 + c.merchant_contacted_days_before_filing)
                    if c.merchant_contacted_days_before_filing is not None
                    else None
                ),
                return_tracking_provided=c.return_tracking_provided,
                merchant_refused_return=c.merchant_refused_return,
            )
        )
        for p in range(c.prior_disputes):  # earlier, unrelated disputes by the same customer
            extra_n += 1
            prior_txn = f"txn_{extra_n:06d}"
            txns.append(
                _charge(
                    c,
                    customer_id,
                    merchant_id,
                    prior_txn,
                    now - timedelta(days=20 + 10 * p),
                    status=TxnStatus.SETTLED,
                    cents=c.amount_cents + 137 * (p + 1),
                )
            )
            disputes.append(
                Case(
                    case_id=f"case_{1000 + i * 10 + p:04d}",
                    txn_id=prior_txn,
                    customer_id=customer_id,
                    reason_code=ReasonCode.NOT_RECEIVED,
                    amount_cents=c.amount_cents + 137 * (p + 1),
                    customer_narrative="Earlier dispute (synthetic).",
                    status=CaseStatus.RESOLVED_REJECTED,
                    opened_at=now - timedelta(days=15 + 10 * p),
                    updated_at=now - timedelta(days=10 + 10 * p),
                )
            )
        for f in range(_FILLERS[c.merchant.risk_tier]):
            filler_n += 1
            txns.append(
                Transaction(
                    txn_id=f"txn_{filler_n:06d}",
                    customer_id=FILLER_CUSTOMER,
                    merchant_id=merchant_id,
                    amount_cents=2_000 + f,
                    channel=Channel.ONLINE,
                    status=TxnStatus.SETTLED,
                    description="Routine purchase",
                    posted_at=now - timedelta(days=1 + f % 80),
                )
            )
    data = SeedData(
        customers=customers,
        merchants=merchants,
        transactions=txns,
        shipments=shipments,
        cases=disputes,
    )
    return World(InMemoryStore(data), case_ids)
