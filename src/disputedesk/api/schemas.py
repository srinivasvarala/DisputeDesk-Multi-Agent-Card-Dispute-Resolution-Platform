"""Request and response models for the HTTP API."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from disputedesk.db.models import CaseId, CaseStatus
from disputedesk.graph.state import ResolutionDraft, Review, Route


class StartDispute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: CaseId = Field(description="An existing open case, e.g. case_0001")


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    approver: str = Field(min_length=1, max_length=100)
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


class Accepted(BaseModel):
    case_id: str
    workflow: WorkflowState
