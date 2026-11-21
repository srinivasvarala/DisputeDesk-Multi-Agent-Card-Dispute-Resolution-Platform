"""Domain records shared by the seed script, the stores and the MCP servers.

All data is synthetic. Money is integer cents; timestamps are timezone-aware UTC.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

# --- ID types (format is part of the contract; tools reject anything else) ---
CustomerId = Annotated[str, StringConstraints(pattern=r"^cus_\d{4}$")]
MerchantId = Annotated[str, StringConstraints(pattern=r"^mer_\d{3}$")]
TxnId = Annotated[str, StringConstraints(pattern=r"^txn_\d{6}$")]
ShipmentId = Annotated[str, StringConstraints(pattern=r"^shp_\d{6}$")]
CaseId = Annotated[str, StringConstraints(pattern=r"^case_\d{4}$")]


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


UtcDatetime = Annotated[datetime, AfterValidator(_require_utc)]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MerchantCategory(StrEnum):
    ELECTRONICS = "electronics"
    APPAREL = "apparel"
    HOME_GOODS = "home_goods"
    TRAVEL = "travel"
    STREAMING = "streaming"
    SOFTWARE = "software"
    RESTAURANT = "restaurant"
    GROCERY = "grocery"


class RiskTier(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Channel(StrEnum):
    ONLINE = "online"
    CARD_PRESENT = "card_present"


class TxnStatus(StrEnum):
    SETTLED = "settled"
    REFUNDED = "refunded"


class ShipmentStatus(StrEnum):
    DELIVERED = "delivered"
    IN_TRANSIT = "in_transit"
    LOST = "lost"
    RETURNED = "returned"


class ReasonCode(StrEnum):
    FRAUD = "fraud"
    NOT_RECEIVED = "not_received"
    DUPLICATE = "duplicate"
    NOT_AS_DESCRIBED = "not_as_described"


class EntryMode(StrEnum):
    """How a card-present charge was read (rulebook 03). Unset for online charges."""

    CHIP_PIN = "chip_pin"
    CHIP_NO_PIN = "chip_no_pin"
    MAGSTRIPE_FALLBACK = "magstripe_fallback"
    KEY_ENTERED = "key_entered"
    CONTACTLESS = "contactless"


class CaseStatus(StrEnum):
    OPEN = "open"
    IN_REVIEW = "in_review"
    PENDING_APPROVAL = "pending_approval"
    ESCALATED = "escalated"
    RESOLVED_ACCEPTED = "resolved_accepted"
    RESOLVED_REJECTED = "resolved_rejected"


class Decision(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    ESCALATE = "escalate"


class Customer(Record):
    customer_id: CustomerId
    name: str
    email: str
    card_last4: Annotated[str, StringConstraints(pattern=r"^\d{4}$")]
    created_at: UtcDatetime


class Merchant(Record):
    merchant_id: MerchantId
    name: str
    category: MerchantCategory
    mcc: Annotated[str, StringConstraints(pattern=r"^\d{4}$")]
    country: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
    ships_physical_goods: bool
    risk_tier: RiskTier
    onboarded_at: UtcDatetime


class Transaction(Record):
    txn_id: TxnId
    customer_id: CustomerId
    merchant_id: MerchantId
    amount_cents: int = Field(gt=0)
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] = "USD"
    channel: Channel
    status: TxnStatus
    description: str
    posted_at: UtcDatetime
    # System record from the terminal; None = not recorded (always None for online charges).
    entry_mode: EntryMode | None = None


class Shipment(Record):
    shipment_id: ShipmentId
    txn_id: TxnId
    carrier: str
    tracking_number: str
    status: ShipmentStatus
    shipped_at: UtcDatetime
    delivered_at: UtcDatetime | None = None
    # From the carrier. None = not recorded (rulebook 04 needs both to treat tracking as proof).
    delivery_address_matches: bool | None = None
    signature_confirmed: bool | None = None


class Case(Record):
    case_id: CaseId
    txn_id: TxnId
    customer_id: CustomerId
    reason_code: ReasonCode
    amount_cents: int = Field(gt=0)
    customer_narrative: str
    status: CaseStatus
    opened_at: UtcDatetime
    updated_at: UtcDatetime
    # Fraud filing items the cardholder confirmed (rulebook 02). None = not recorded.
    fraud_declaration_signed: bool | None = None
    card_reissued: bool | None = None
    card_reported_lost_at: UtcDatetime | None = None
    # Not-as-described filing items (rulebook 07). None = not recorded.
    merchant_contacted_at: UtcDatetime | None = None  # starts the 15-day wait
    return_tracking_provided: bool | None = None  # carrier tracking for the returned goods
    merchant_refused_return: bool | None = None  # refused, or gave no return address: a waiver


class CaseDraft(Record):
    case_id: CaseId
    version: int = Field(ge=1)
    decision: Decision
    rationale: str
    letter: str
    author: str
    created_at: UtcDatetime


class AuditOutcome(StrEnum):
    OK = "ok"
    INVALID_INPUT = "invalid_input"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    ERROR = "error"
    CANCELLED = "cancelled"
    DENIED = "denied"  # missing or invalid credential, or a tool the caller was not granted


class AuditEntry(Record):
    server: str
    tool: str
    actor: str
    case_id: str | None
    arguments: dict[str, Any]
    outcome: AuditOutcome
    error: str | None
    duration_ms: int = Field(ge=0)
    created_at: UtcDatetime
