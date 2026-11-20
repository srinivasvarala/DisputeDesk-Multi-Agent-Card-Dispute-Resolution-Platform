"""FastAPI app: start a dispute, read its progress, and approve or reject it.

    POST /disputes                  start the workflow for an open case (runs in the background)
    GET  /disputes/{id}             case status, workflow state, draft, review and route taken
    POST /disputes/{id}/approve     give the human decision a paused case is waiting for

Callers are not authenticated yet; JWT roles arrive in M5 (the `approver` field is then taken
from the token, not the body).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, status

from disputedesk.api.runtime import (
    CaseNotFoundError,
    Runtime,
    WorkflowConflictError,
    open_runtime,
)
from disputedesk.api.schemas import (
    Accepted,
    ApprovalRequest,
    DisputeView,
    StartDispute,
    WorkflowState,
)


def _not_found(case_id: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"case {case_id} not found")


def create_app(runtime: Runtime | None = None) -> FastAPI:
    """Pass a `Runtime` in tests; by default one is opened from settings at startup."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if runtime is not None:
            yield
            return
        with open_runtime() as opened:
            app.state.runtime = opened
            yield

    app = FastAPI(title="DisputeDesk", version="0.1.0", lifespan=lifespan)
    if runtime is not None:
        app.state.runtime = runtime

    def rt() -> Runtime:
        runtime_: Runtime = app.state.runtime
        return runtime_

    @app.post("/disputes", status_code=status.HTTP_202_ACCEPTED)
    def start_dispute(body: StartDispute, background: BackgroundTasks) -> Accepted:
        try:
            rt().claim_start(body.case_id)
        except CaseNotFoundError:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"case {body.case_id} not found"
            ) from None
        except WorkflowConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
        background.add_task(rt().run, body.case_id)
        return Accepted(case_id=body.case_id, workflow=WorkflowState.RUNNING)

    @app.get("/disputes/{case_id}")
    def get_dispute(case_id: str) -> DisputeView:
        try:
            return rt().view(case_id)
        except CaseNotFoundError:
            raise _not_found(case_id) from None

    @app.post("/disputes/{case_id}/approve", status_code=status.HTTP_202_ACCEPTED)
    def approve_dispute(
        case_id: str, body: ApprovalRequest, background: BackgroundTasks
    ) -> Accepted:
        try:
            rt().claim_resume(case_id)
        except CaseNotFoundError:
            raise _not_found(case_id) from None
        except WorkflowConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
        background.add_task(rt().resume, case_id, body)
        return Accepted(case_id=case_id, workflow=WorkflowState.RUNNING)

    return app


def app() -> FastAPI:
    """Factory for `uvicorn disputedesk.api.app:app --factory`."""
    return create_app()
