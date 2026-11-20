"""Graph state and the structured outputs each node produces. All Pydantic.

Nodes never mutate state in place: each returns a dict with only the fields it changes.
Fields that hold model output are small and typed, so a checkpoint is easy to read and replay.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from disputedesk.agents.policy import PolicyAnswer
from disputedesk.db.models import CaseStatus, Decision, ReasonCode, RiskTier

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


class Route(Model):
    """Router output. `risk_tier` is computed by plain Python rules, not by the LLM."""

    reason_code: ReasonCode
    risk_tier: RiskTier
    approval_required: bool
    rationale: str = Field(min_length=1, max_length=500)


class Evidence(Model):
    """What the Evidence agent gathered. Facts only; no decision."""

    summary: str = Field(min_length=1)


class ResolutionDraft(Model):
    decision: Decision
    rationale: str = Field(min_length=1, max_length=4_000)
    letter: str = Field(min_length=1, max_length=8_000)
    version: int = Field(ge=1, description="Draft version saved through the cases server")


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
    path: list[str] = Field(default_factory=list, description="Nodes visited, in order")
