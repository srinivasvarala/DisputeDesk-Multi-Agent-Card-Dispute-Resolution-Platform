"""FastAPI app: start a dispute, read its progress, and approve or reject it.

    GET  /disputes                  case list, optionally filtered by status
    POST /disputes                  start the workflow for an open case (runs in the background)
    GET  /disputes/{id}             case status, workflow state, draft, review and route taken
    POST /disputes/{id}/approve     give the human decision a paused case is waiting for

Every route needs a bearer JWT whose role grants the route's scope (see security/scopes.py).
The approver is the token's subject, never a field in the request body.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, status

from disputedesk.api.auth import Authenticator
from disputedesk.api.runtime import (
    CaseNotFoundError,
    Runtime,
    WorkflowConflictError,
    open_runtime,
)
from disputedesk.api.schemas import (
    Accepted,
    ApprovalRequest,
    CaseSummary,
    DisputeView,
    StartDispute,
    WorkflowState,
)
from disputedesk.config import Settings, get_settings
from disputedesk.db.models import AuditEntry, AuditOutcome, CaseId, CaseStatus
from disputedesk.observability.otel import instrument_app, setup_telemetry
from disputedesk.security.jwt import Principal, require_jwt_config
from disputedesk.security.scopes import Scope

logger = logging.getLogger(__name__)

AUDIT_SERVER = "api"


def _actor(principal: Principal) -> str:
    """Prefixed so a user whose `sub` is an agent's name cannot pass for that agent in the log."""
    return f"user:{principal.sub}"


def _not_found(case_id: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"case {case_id} not found")


def create_app(runtime: Runtime | None = None, settings: Settings | None = None) -> FastAPI:
    """Pass a `Runtime` in tests; by default one is opened from settings at startup.

    Raises at startup if JWT_SECRET is missing or too short.
    """
    settings = settings or get_settings()
    require_jwt_config(settings)
    setup_telemetry(settings)  # before any DB connection so psycopg is instrumented

    def audit(
        actor: str,
        tool: str,
        *,
        case_id: str | None,
        arguments: dict[str, Any],
        outcome: AuditOutcome = AuditOutcome.OK,
        error: str | None = None,
    ) -> None:
        rt().store.write_audit(
            AuditEntry(
                server=AUDIT_SERVER,
                tool=tool,
                actor=actor,
                case_id=case_id,
                arguments=arguments,
                outcome=outcome,
                error=error,
                duration_ms=0,
                created_at=datetime.now(UTC),
            )
        )

    def audit_denied(
        principal: Principal | None, scope: Scope, request: Request, code: int
    ) -> None:
        """Authenticated 403s are audited. Anonymous 401s are only logged: audit_log is
        append-only, so one row per unauthenticated request would let anyone fill it forever.
        A broken audit log must not turn a denial into a success."""
        if principal is None:
            logger.warning("401 %s %s", request.method, request.url.path)
            return
        route = request.scope.get("route")
        template = getattr(route, "path", request.url.path)
        case_id = request.path_params.get("case_id")
        try:
            audit(
                _actor(principal),
                f"{request.method} {template}",
                case_id=case_id if isinstance(case_id, str) else None,
                arguments={
                    "scope": scope.value,
                    "status": code,
                    "role": principal.role.value,
                },
                outcome=AuditOutcome.DENIED,
                error="insufficient scope",
            )
        except Exception:
            logger.exception("could not audit a denied request")

    auth = Authenticator(settings, on_denied=audit_denied)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if runtime is not None:
            yield
            return
        with open_runtime() as opened:
            app.state.runtime = opened
            yield

    app = FastAPI(title="DisputeDesk", version="0.1.0", lifespan=lifespan)
    instrument_app(app)
    if runtime is not None:
        app.state.runtime = runtime

    def rt() -> Runtime:
        runtime_: Runtime = app.state.runtime
        return runtime_

    @app.post("/disputes", status_code=status.HTTP_202_ACCEPTED)
    def start_dispute(
        body: StartDispute,
        background: BackgroundTasks,
        principal: Principal = Depends(auth.require(Scope.DISPUTES_START)),  # noqa: B008
    ) -> Accepted:
        try:
            rt().claim_start(body.case_id)
        except CaseNotFoundError:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"case {body.case_id} not found"
            ) from None
        except WorkflowConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
        try:
            audit(
                _actor(principal),
                "start_dispute",
                case_id=body.case_id,
                arguments={"role": principal.role.value},
            )
        except Exception:
            rt().release(body.case_id)  # the run will never start; do not leave the case claimed
            raise
        background.add_task(rt().run, body.case_id)
        return Accepted(case_id=body.case_id, workflow=WorkflowState.RUNNING)

    @app.get("/disputes")
    def list_disputes(
        case_status: CaseStatus | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        _: Principal = Depends(auth.require(Scope.DISPUTES_READ)),  # noqa: B008
    ) -> list[CaseSummary]:
        # A read of case state, like GET /disputes/{id}: not audited (CLAUDE.md).
        cases = rt().store.list_cases()
        return [
            CaseSummary(
                case_id=c.case_id,
                reason_code=c.reason_code,
                amount_cents=c.amount_cents,
                case_status=c.status,
                opened_at=c.opened_at,
            )
            for c in cases
            if case_status is None or c.status == case_status
        ][:limit]

    @app.get("/disputes/{case_id}")
    def get_dispute(
        case_id: str,
        _: Principal = Depends(auth.require(Scope.DISPUTES_READ)),  # noqa: B008
    ) -> DisputeView:
        try:
            return rt().view(case_id)
        except CaseNotFoundError:
            raise _not_found(case_id) from None

    @app.post("/disputes/{case_id}/approve", status_code=status.HTTP_202_ACCEPTED)
    def approve_dispute(
        case_id: str,
        body: ApprovalRequest,
        background: BackgroundTasks,
        principal: Principal = Depends(auth.require(Scope.DISPUTES_APPROVE)),  # noqa: B008
    ) -> Accepted:
        try:
            rt().claim_resume(case_id)
        except CaseNotFoundError:
            raise _not_found(case_id) from None
        except WorkflowConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
        try:
            audit(
                _actor(principal),
                "approve_dispute",
                case_id=case_id,
                arguments={"role": principal.role.value, "approved": body.approved},
            )
        except Exception:
            rt().release(case_id)  # the resume will never start; the case stays approvable
            raise
        background.add_task(rt().resume, case_id, body, principal.sub)
        return Accepted(case_id=case_id, workflow=WorkflowState.RUNNING)

    @app.get("/audit")
    def read_audit(
        case_id: CaseId | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        principal: Principal = Depends(auth.require(Scope.AUDIT_READ)),  # noqa: B008
    ) -> list[AuditEntry]:
        # Fail-closed and written first: nobody reads the log without leaving a row in it.
        audit(
            _actor(principal),
            "read_audit",
            case_id=case_id,
            arguments={"role": principal.role.value, "limit": limit},
        )
        return rt().store.read_audit(case_id=case_id, limit=limit)

    return app


def app() -> FastAPI:
    """Factory for `uvicorn disputedesk.api.app:app --factory`."""
    return create_app()
