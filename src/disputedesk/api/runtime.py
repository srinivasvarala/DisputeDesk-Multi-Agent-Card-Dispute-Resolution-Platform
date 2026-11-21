"""What the API needs at run time: the compiled graph, the case store and run bookkeeping.

`Runtime` takes its parts as arguments, so tests can pass in-memory ones. `open_runtime`
builds the real ones from settings: Postgres store, Postgres checkpointer, the RAG retriever
and the configured LLM.

One process runs the graph. `claim` stops two requests from driving the same case at once;
across several API processes this would need a database lock instead (a known gap).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from opentelemetry.trace import Span, SpanContext, Status, StatusCode
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from disputedesk.agents import allowlists
from disputedesk.agents.common import DecisionAuditor
from disputedesk.agents.fake_llm import demo_responder
from disputedesk.agents.policy import PolicySearch
from disputedesk.agents.toolbox import ToolBox, tool_index
from disputedesk.api.schemas import ApprovalRequest, DisputeView, WorkflowState
from disputedesk.config import Settings, get_settings
from disputedesk.db import apply_schema, connect
from disputedesk.db.models import AuditOutcome, CaseStatus
from disputedesk.graph.builder import build_graph
from disputedesk.graph.output_guard import OutputCheck
from disputedesk.graph.serde import checkpoint_serde
from disputedesk.graph.state import DisputeState
from disputedesk.graph.system_nodes import InputCheck
from disputedesk.graph.wiring import Dependencies, build_nodes
from disputedesk.llm import FakeClient, LLMClient, get_embedder, get_llm, get_reranker
from disputedesk.mcp_servers.cases import server as cases_server
from disputedesk.mcp_servers.common.store import (
    AuditReader,
    AuditSink,
    CaseWriteStore,
    PostgresStore,
    ReadStore,
)
from disputedesk.mcp_servers.merchant import server as merchant_server
from disputedesk.mcp_servers.transactions import server as transactions_server
from disputedesk.observability.langfuse import make_langfuse, update_trace
from disputedesk.observability.otel import CaseTotals, case_span, record_case_run
from disputedesk.rag.retriever import Retriever
from disputedesk.security.agent_tokens import CredentialIssuer, CredentialVerifier

logger = logging.getLogger(__name__)

Graph = CompiledStateGraph[DisputeState, None, DisputeState, DisputeState]


class FullStore(ReadStore, CaseWriteStore, AuditSink, AuditReader, Protocol):
    """Everything `PostgresStore` and `InMemoryStore` both provide."""


class CaseNotFoundError(LookupError):
    pass


class WorkflowConflictError(RuntimeError):
    """The request does not fit the workflow's current state (already started, not waiting...)."""


class MakerCheckerError(PermissionError):
    """The person who started a case tried to approve it."""


@dataclass
class Runtime:
    graph: Graph
    store: FullStore
    failure_tools: ToolBox  # used only to escalate a case whose workflow crashed
    _active: set[str] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    # Trace of each case's first run, so a resume after human approval joins the same trace.
    # In memory: after an API restart a resume starts a new trace (known gap, see PROGRESS.md).
    _trace_roots: dict[str, SpanContext] = field(default_factory=dict)
    _totals: dict[str, CaseTotals] = field(default_factory=dict)  # cost etc. across a pause
    langfuse: Any | None = None  # Langfuse client for trace-level input/output

    # --- reading -------------------------------------------------------------------------

    def view(self, case_id: str) -> DisputeView:
        case = self.store.get_case(case_id)
        if case is None:
            raise CaseNotFoundError(case_id)
        snap = self.graph.get_state(_config(case_id))
        if not snap.values:
            return DisputeView(
                case_id=case_id, case_status=case.status, workflow=WorkflowState.NOT_STARTED
            )
        state = DisputeState.model_validate(snap.values)
        # `snap.next` being empty is NOT proof of completion: between two nodes LangGraph can
        # checkpoint before the next conditional edge is resolved. Only finalize/escalate set
        # `final_status`, so that is the completion signal.
        if state.final_status is not None:
            workflow = WorkflowState.COMPLETED
        elif case.status == CaseStatus.ESCALATED:
            # A crashed run escalates the case. Checked before the interrupt: a run that
            # crashed while resuming still has its old interrupt in the checkpoint.
            workflow = WorkflowState.FAILED
        elif any(task.interrupts for task in snap.tasks):
            workflow = WorkflowState.AWAITING_APPROVAL
        else:
            workflow = WorkflowState.RUNNING
        return DisputeView(
            case_id=case_id,
            case_status=case.status,
            workflow=workflow,
            route=state.route,
            draft=state.draft,
            review=state.review,
            revision_count=state.revision_count,
            input_flag=state.input_flag,
            started_by=state.started_by,
            path=state.path,
        )

    # --- driving -------------------------------------------------------------------------

    def claim_start(self, case_id: str) -> None:
        """Reserve a new run. Raises CaseNotFoundError / WorkflowConflictError.

        The state check and the claim happen under one lock, so two requests cannot both pass
        the check and then both run."""
        with self._lock:
            case = self.store.get_case(case_id)
            if case is None:
                raise CaseNotFoundError(case_id)
            if case.status != CaseStatus.OPEN or self.graph.get_state(_config(case_id)).values:
                raise WorkflowConflictError(
                    f"case {case_id} is {case.status}; only open cases start"
                )
            self._claim_locked(case_id)

    def claim_resume(self, case_id: str, approver: str) -> None:
        """Reserve a resume. Raises CaseNotFoundError, WorkflowConflictError or MakerCheckerError.

        Maker-checker: whoever started the case cannot approve it. There is no exemption for
        admins. A case with no recorded starter (started before the rule) is not blocked."""
        with self._lock:
            view = self.view(case_id)
            if view.workflow != WorkflowState.AWAITING_APPROVAL:
                raise WorkflowConflictError(f"case {case_id} is not waiting for approval")
            if view.started_by is not None and view.started_by == approver:
                raise MakerCheckerError(
                    "the user who started a case cannot approve it (maker-checker)"
                )
            self._claim_locked(case_id)

    def release(self, case_id: str) -> None:
        """Give back a claim whose run or resume will never be started."""
        with self._lock:
            self._active.discard(case_id)

    def run(self, case_id: str, started_by: str | None = None) -> None:
        """Run a claimed case from the start until it finishes or pauses."""
        self._drive(case_id, DisputeState(case_id=case_id, started_by=started_by), "case.run")

    def resume(self, case_id: str, decision: ApprovalRequest, approver: str) -> None:
        """Continue a claimed, paused case with the human's decision."""
        resume: Command[Any] = Command(resume={**decision.model_dump(), "approver": approver})
        self._drive(case_id, resume, "case.resume")

    def _claim_locked(self, case_id: str) -> None:
        if case_id in self._active:
            raise WorkflowConflictError(f"case {case_id} is already being processed")
        self._active.add(case_id)

    def _drive(
        self, case_id: str, graph_input: DisputeState | Command[Any], span_name: str
    ) -> None:
        paused = False
        try:
            with case_span(
                span_name,
                case_id,
                parent=self._trace_roots.get(case_id),
                totals=self._totals.setdefault(case_id, CaseTotals()),
            ) as (span, totals):
                self._trace_roots.setdefault(case_id, span.get_span_context())
                ctx = span.get_span_context()
                # No SDK (OTEL_ENABLED unset) means an all-zero id: skip Langfuse rather than
                # write every case into one trace.
                trace_id = format(ctx.trace_id, "032x") if ctx.is_valid else None
                if trace_id and span_name == "case.run":
                    update_trace(
                        self.langfuse, trace_id, case_id=case_id, input={"case_id": case_id}
                    )
                try:
                    result = self.graph.invoke(graph_input, _config(case_id))
                except Exception as exc:
                    # Type only: the message can echo the model's reply, and so the narrative.
                    span.set_attribute("error.type", type(exc).__name__)
                    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                    logger.exception("workflow failed for %s", case_id)
                    self._fail(case_id, exc)
                else:
                    paused = isinstance(result, dict) and "__interrupt__" in result
                    try:  # telemetry must never decide the case outcome
                        self._record_run(span, trace_id, case_id, result, totals, paused)
                    except Exception:
                        logger.exception("could not record telemetry for %s", case_id)
        finally:
            if not paused:  # finished or failed: nothing will resume this trace or its totals
                self._trace_roots.pop(case_id, None)
                self._totals.pop(case_id, None)
            with self._lock:
                self._active.discard(case_id)

    def _record_run(
        self,
        span: Span,
        trace_id: str | None,
        case_id: str,
        result: Any,
        totals: CaseTotals,
        paused: bool,
    ) -> None:
        state = _as_state(result)
        tier = str(state.route.risk_tier) if state.route else None
        span.set_attribute("case.revision_loops", state.revision_count)
        if tier:
            span.set_attribute("risk_tier", tier)
        if not paused:  # one data point per case, not per run segment
            record_case_run(totals, state.revision_count, tier)
        if trace_id:
            update_trace(
                self.langfuse, trace_id, case_id=case_id, output=_run_summary(state, result, totals)
            )

    def _fail(self, case_id: str, exc: Exception) -> None:
        """Do not leave a crashed case in review: escalate it and say why in the audit log."""
        reason = f"workflow failed: {type(exc).__name__}"
        # Only the exception type goes to the audit log: messages from validation or parsing
        # errors can echo customer text. The full detail is in the application log above.
        DecisionAuditor(self.store, allowlists.SYSTEM).record(
            "workflow_failed", case_id, {}, outcome=AuditOutcome.ERROR, error=type(exc).__name__
        )
        try:
            case = self.store.get_case(case_id)
            if case is not None and case.status == CaseStatus.ESCALATED:
                return  # already escalated (the crash came after it): moving again is invalid
            self.failure_tools.call(
                "update_status",
                {"case_id": case_id, "new_status": CaseStatus.ESCALATED, "reason": reason},
            )
        except Exception:
            logger.exception("could not escalate failed case %s", case_id)


def _run_summary(state: DisputeState, result: Any, totals: CaseTotals) -> dict[str, Any]:
    """Facts about how a run ended, for the Langfuse trace output. No free text from the case."""
    paused = isinstance(result, dict) and "__interrupt__" in result
    return {
        "outcome": "awaiting_approval" if paused else str(state.final_status or "finished"),
        "decision": str(state.draft.decision) if state.draft else None,
        "reason_code": str(state.route.reason_code) if state.route else None,
        "risk_tier": str(state.route.risk_tier) if state.route else None,
        "revision_loops": state.revision_count,
        "path": state.path,
        "llm_calls": totals.llm_calls,
        "cost_usd": round(totals.cost_usd, 6),
    }


def _as_state(result: Any) -> DisputeState:
    """`graph.invoke` returns the state as a model or a dict, depending on the LangGraph version."""
    if isinstance(result, DisputeState):
        return result
    # A paused run also carries an `__interrupt__` entry that is not part of the state.
    return DisputeState.model_validate(
        {k: v for k, v in result.items() if k in DisputeState.model_fields}
    )


def _config(case_id: str) -> Any:
    return {"configurable": {"thread_id": case_id}}


def build_runtime(
    *,
    settings: Settings,
    llm: LLMClient,
    retriever: PolicySearch,
    store: FullStore,
    checkpointer: BaseCheckpointSaver[Any] | None,
    langfuse: Any | None = None,
    input_check: InputCheck | None = None,
    output_check: OutputCheck | None = None,
) -> Runtime:
    issuer = CredentialIssuer(settings)  # raises if JWT_SECRET is missing or weak
    verifier = CredentialVerifier(settings)
    servers = [
        transactions_server.build_server(store, store, verifier=verifier),
        merchant_server.build_server(store, store, verifier=verifier),
        cases_server.build_server(store, store, store, verifier=verifier),
    ]
    deps = Dependencies(
        settings=settings,
        llm=llm,
        retriever=retriever,
        servers=servers,
        audit=store,
        langfuse=langfuse,
        issuer=issuer,
        input_check=input_check,
        output_check=output_check,
    )
    graph = build_graph(build_nodes(deps), checkpointer)
    failure_tools = ToolBox(
        agent=allowlists.SYSTEM,
        allowed=allowlists.ALLOWLISTS[allowlists.SYSTEM],
        tools=tool_index(servers),
        audit=store,
        timeout=settings.tool_timeout_seconds,
        retries=settings.tool_max_retries,
        issuer=issuer,
    )
    return Runtime(graph=graph, store=store, failure_tools=failure_tools, langfuse=langfuse)


@contextmanager
def open_runtime(settings: Settings | None = None) -> Iterator[Runtime]:
    """The real thing: Postgres for data and checkpoints, real retriever, configured LLM."""
    settings = settings or get_settings()
    llm: LLMClient = (
        FakeClient(demo_responder) if settings.llm_provider == "fake" else get_llm(settings)
    )
    with (
        connect(settings.database_url) as conn,
        connect(settings.database_url) as rag_conn,
        ConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=4,
            kwargs={"autocommit": True, "row_factory": dict_row},
            open=True,
        ) as pool,
    ):
        apply_schema(conn)
        checkpointer = PostgresSaver(pool, serde=checkpoint_serde())  # type: ignore[arg-type]
        checkpointer.setup()
        retriever = Retriever(rag_conn, get_embedder(settings), get_reranker(settings))
        yield build_runtime(
            settings=settings,
            llm=llm,
            retriever=retriever,
            store=PostgresStore(conn),
            checkpointer=checkpointer,
            langfuse=make_langfuse(settings),
        )
