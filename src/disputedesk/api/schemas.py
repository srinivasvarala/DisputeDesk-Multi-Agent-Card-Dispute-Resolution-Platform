"""Request and response models for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from disputedesk.db.models import CaseId, CaseStatus, ReasonCode
from disputedesk.graph.state import ResolutionDraft, Review, Route


class StartDispute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: CaseId = Field(description="An existing open case, e.g. case_0001")


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool  # the approver's identity comes from the token, not the body
    note: str = Field(default="", max_length=1_000)


class WorkflowState(StrEnum):
    NOT_STARTED = "not_started"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"


class DisputeView(BaseModel):
    case_id: str
    case_status: CaseStatus
    workflow: WorkflowState
    route: Route | None = None
    draft: ResolutionDraft | None = None
    review: Review | None = None
    revision_count: int = 0
    path: list[str] = Field(default_factory=list, description="Graph nodes visited, in order")


class CaseSummary(BaseModel):
    """One row of the case list. Workflow detail is in `DisputeView`, fetched per case."""

    case_id: str
    reason_code: ReasonCode
    amount_cents: int
    case_status: CaseStatus
    opened_at: datetime


class Accepted(BaseModel):
    case_id: str
    workflow: WorkflowState
