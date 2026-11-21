"""Graph state and the structured outputs each node produces. All Pydantic.

Nodes never mutate state in place: each returns a dict with only the fields it changes.
Fields that hold model output are small and typed, so a checkpoint is easy to read and replay.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from disputedesk.agents.policy import PolicyAnswer
from disputedesk.db.models import CaseStatus, Decision, Merchant, ReasonCode, RiskTier, Transaction
from disputedesk.mcp_servers.merchant.server import DeliveryStatus, DisputeRate
from disputedesk.mcp_servers.transactions.server import CustomerHistory

MAX_REVISIONS = 2  # reviewer may send a draft back at most this many times


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CaseFacts(Model):
    """The case record as intake read it. `narrative` is untrusted customer text."""

    txn_id: str
    customer_id: str
    amount_cents: int = Field(gt=0)
    claimed_reason: ReasonCode
    narrative: str
    filed_at: datetime | None = None  # the filing date (the case's opened_at)
    # Fraud filing items, as recorded at filing. None = not recorded.
    fraud_declaration_signed: bool | None = None
    card_reissued: bool | None = None
    card_reported_lost_at: datetime | None = None


class Route(Model):
    """Router output. `risk_tier` is computed by plain Python rules, not by the LLM."""

    reason_code: ReasonCode
    risk_tier: RiskTier
    approval_required: bool
    rationale: str = Field(min_length=1, max_length=500)


class Leaning(StrEnum):
    SUPPORTS_CUSTOMER = "supports_customer"
    SUPPORTS_MERCHANT = "supports_merchant"
    INCONCLUSIVE = "inconclusive"


class Evidence(Model):
    """What the Evidence agent gathered. The facts come straight from tool results; only
    `summary` and `leaning` are the model's reading of them."""

    transaction: Transaction
    merchant: Merchant
    customer_history: CustomerHistory
    merchant_dispute_rate: DisputeRate
    delivery: DeliveryStatus | None = None
    possible_duplicates: list[Transaction] = Field(default_factory=list)
    summary: str = Field(min_length=1, max_length=1_500)
    leaning: Leaning


class ResolutionDraft(Model):
    decision: Decision
    rationale: str = Field(min_length=1, max_length=4_000)
    letter: str = Field(min_length=1, max_length=8_000)
    version: int = Field(ge=1, description="Draft version saved through the cases server")
    confidence: float | None = Field(default=None, ge=0, le=1, description="Analyst's own, 0 to 1")


class Verdict(StrEnum):
    APPROVE = "approve"
    REVISE = "revise"


class Review(Model):
    verdict: Verdict
    feedback: str = Field(default="", max_length=2_000)


class HumanDecision(Model):
    approved: bool
    approver: str = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=1_000)


class DisputeState(BaseModel):
    """The whole graph state. Checkpointed after every node."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    facts: CaseFacts | None = None
    route: Route | None = None
    evidence: Evidence | None = None
    policy: PolicyAnswer | None = None
    draft: ResolutionDraft | None = None
    review: Review | None = None
    revision_count: int = Field(default=0, ge=0)
    human: HumanDecision | None = None
    final_status: CaseStatus | None = None
    # Set at intake when the input guardrail blocks the narrative: the case goes straight to a
    # human and no agent reads the text. Holds the rail that fired, never the text itself.
    input_flag: str | None = None
    path: list[str] = Field(default_factory=list, description="Nodes visited, in order")
