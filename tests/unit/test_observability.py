"""One case = one trace, with the agreed attributes, metrics and a matching Langfuse trace id."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import StatusCode

from disputedesk.agents.toolbox import ToolBox, ToolCallError, ToolDeniedError, tool_index
from disputedesk.api import runtime as runtime_module
from disputedesk.api.runtime import Runtime, build_runtime
from disputedesk.api.schemas import ApprovalRequest
from disputedesk.graph.serde import checkpoint_serde
from disputedesk.mcp_servers.common.audit import AuditedMCPServer
from disputedesk.mcp_servers.common.store import InMemoryStore
from disputedesk.observability.otel import CaseTotals
from tests.conftest import make_settings
from tests.graph_helpers import TEST_JWT_SECRET, StubRetriever, fake_llm, needs_approval
from tests.otel_helpers import install_test_sdk

_SPANS, _READER = install_test_sdk()


class FakeLangfuse:
    def __init__(self) -> None:
        self.traces: list[dict[str, Any]] = []
        self.generations: list[dict[str, Any]] = []

    def trace(self, **kwargs: Any) -> None:
        self.traces.append(kwargs)

    def generation(self, **kwargs: Any) -> None:
        self.generations.append(kwargs)


@pytest.fixture(autouse=True)
def _clear() -> Iterator[None]:
    _SPANS.clear()
    yield


def _runtime(store: InMemoryStore, langfuse: FakeLangfuse | None = None) -> Runtime:
    settings = make_settings(
        jwt_secret=TEST_JWT_SECRET, llm_price_input_per_mtok=3.0, llm_price_output_per_mtok=15.0
    )
    llm, _ = fake_llm()
    return build_runtime(
        settings=settings,
        llm=llm,
        retriever=StubRetriever(),
        store=store,
        checkpointer=InMemorySaver(serde=checkpoint_serde()),
        langfuse=langfuse,
    )


def _run_low_risk_case(store: InMemoryStore) -> tuple[Runtime, str]:
    runtime = _runtime(store)
    settings = make_settings(jwt_secret=TEST_JWT_SECRET)
    case = next(c for c in store.list_cases() if not needs_approval(c, store, settings))
    runtime.claim_start(case.case_id)
    runtime.run(case.case_id)
    return runtime, case.case_id


def _by_name(spans: tuple[ReadableSpan, ...], prefix: str) -> list[ReadableSpan]:
    return [s for s in spans if s.name.startswith(prefix)]


def test_one_case_is_one_connected_trace(store: InMemoryStore) -> None:
    _, case_id = _run_low_risk_case(store)
    spans = _SPANS.get_finished_spans()

    assert len({s.context.trace_id for s in spans}) == 1
    root = _by_name(spans, "case.run")[0]
    assert root.parent is None
    assert root.attributes is not None and root.attributes["case_id"] == case_id
    by_id = {s.context.span_id: s for s in spans}
    for s in spans:  # every other span hangs off a span of the same trace
        assert s is root or (s.parent is not None and s.parent.span_id in by_id)
    kinds = {s.name.split(".")[0] for s in spans}
    assert {"case", "agent", "tool", "llm"} <= kinds


def test_spans_carry_case_id_agent_and_risk_tier(store: InMemoryStore) -> None:
    _, case_id = _run_low_risk_case(store)
    spans = _SPANS.get_finished_spans()

    agents = {s.name: s for s in _by_name(spans, "agent.")}
    for name in ("agent.router", "agent.evidence", "agent.resolution", "agent.reviewer"):
        attrs = agents[name].attributes
        assert attrs is not None
        assert attrs["case_id"] == case_id and attrs["agent"] == name.removeprefix("agent.")
    assert agents["agent.router"].attributes["risk_tier"]  # type: ignore[index]
    for name in ("agent.evidence", "agent.resolution", "agent.reviewer"):
        assert agents[name].attributes["risk_tier"]  # type: ignore[index]
    tool = _by_name(spans, "tool.")[0]
    assert tool.attributes is not None and tool.attributes["agent"] and tool.attributes["case_id"]
    llm = _by_name(spans, "llm.")[0]
    assert llm.attributes is not None and llm.attributes["agent"] != "unknown"


def test_guardrail_triggered_lands_on_a_span(store: InMemoryStore) -> None:
    _run_low_risk_case(store)
    flagged = [
        e
        for s in _SPANS.get_finished_spans()
        for e in s.events
        if e.name == "guardrail" and e.attributes and "guardrail.triggered" in e.attributes
    ]
    assert flagged  # every check is recorded, triggered or not


def _metric_names() -> set[str]:
    data = _READER.get_metrics_data()
    assert data is not None
    return {m.name for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics}


def test_metrics_are_recorded(store: InMemoryStore) -> None:
    _run_low_risk_case(store)
    assert {
        "agent.latency",
        "guardrail.checks",
        "case.cost_usd",
        "case.revision_loops",
    } <= _metric_names()


def test_cost_per_case_is_on_the_root_span(store: InMemoryStore) -> None:
    _run_low_risk_case(store)
    root = _by_name(_SPANS.get_finished_spans(), "case.run")[0]
    assert root.attributes is not None
    assert root.attributes["case.cost_usd"] > 0  # type: ignore[operator]


def test_langfuse_generations_use_the_otel_trace_id_and_are_redacted(
    store: InMemoryStore,
) -> None:
    lf = FakeLangfuse()
    runtime = _runtime(store, lf)
    settings = make_settings(jwt_secret=TEST_JWT_SECRET)
    case = next(c for c in store.list_cases() if not needs_approval(c, store, settings))
    runtime.claim_start(case.case_id)
    runtime.run(case.case_id)

    trace_id = format(_by_name(_SPANS.get_finished_spans(), "case.run")[0].context.trace_id, "032x")
    assert lf.generations
    assert {g["trace_id"] for g in lf.generations} == {trace_id}
    assert {t["id"] for t in lf.traces} == {trace_id}
    assert all(t["session_id"] == case.case_id for t in lf.traces)
    # The trace-level Input / Output are set too (facts about the run, not customer text).
    assert any(t.get("input") == {"case_id": case.case_id} for t in lf.traces)
    outputs = [t["output"] for t in lf.traces if t.get("output")]
    assert len(outputs) == 1
    out = outputs[0]
    assert out["outcome"] and out["decision"] and out["path"][0] == "intake"
    assert out["llm_calls"] == len(lf.generations) and out["cost_usd"] > 0
    assert "letter" not in out and "rationale" not in out
    first = lf.generations[0]
    assert first["usage"]["total_cost"] > 0 and first["usage"]["unit"] == "TOKENS"
    assert first["name"] != "unknown"


def test_resume_after_approval_joins_the_same_trace(store: InMemoryStore) -> None:
    runtime = _runtime(store)
    settings = make_settings(jwt_secret=TEST_JWT_SECRET)
    case = next(c for c in store.list_cases() if needs_approval(c, store, settings))
    runtime.claim_start(case.case_id)
    runtime.run(case.case_id)
    runtime.claim_resume(case.case_id, "approver-1")
    runtime.resume(case.case_id, ApprovalRequest(approved=True, note="ok"), "approver-1")

    spans = _SPANS.get_finished_spans()
    assert len({s.context.trace_id for s in spans}) == 1
    assert _by_name(spans, "case.run") and _by_name(spans, "case.resume")


def test_a_paused_and_resumed_case_adds_its_cost_up_and_records_it_once(
    store: InMemoryStore,
) -> None:
    lf = FakeLangfuse()
    runtime = _runtime(store, lf)
    settings = make_settings(jwt_secret=TEST_JWT_SECRET)
    case = next(c for c in store.list_cases() if needs_approval(c, store, settings))
    runtime.claim_start(case.case_id)
    runtime.run(case.case_id)
    runtime.claim_resume(case.case_id, "approver-1")
    runtime.resume(case.case_id, ApprovalRequest(approved=True, note="ok"), "approver-1")

    outputs = [t["output"] for t in lf.traces if t.get("output")]
    final = outputs[-1]
    assert outputs[0]["outcome"] == "awaiting_approval"
    assert final["llm_calls"] == len(lf.generations) > 0  # the first run's calls are included
    assert final["cost_usd"] >= outputs[0]["cost_usd"] > 0
    assert not runtime._totals and not runtime._trace_roots  # cleaned up once the case is done


def test_no_sdk_means_no_langfuse_trace_rather_than_a_shared_zero_id(
    store: InMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    @contextmanager
    def no_sdk_span(*_: Any, totals: CaseTotals | None = None, **__: Any) -> Iterator[Any]:
        yield trace.INVALID_SPAN, totals or CaseTotals()  # what OTEL_ENABLED=false gives

    monkeypatch.setattr(runtime_module, "case_span", no_sdk_span)
    lf = FakeLangfuse()
    runtime = _runtime(store, lf)
    settings = make_settings(jwt_secret=TEST_JWT_SECRET)
    case = next(c for c in store.list_cases() if not needs_approval(c, store, settings))
    runtime.claim_start(case.case_id)
    runtime.run(case.case_id)

    # `_drive` made no trace-level update (those carry the case id as session), and nothing
    # was written to the all-zero id.
    assert not [t for t in lf.traces if t.get("session_id") == case.case_id]
    assert not [t for t in lf.traces if t.get("id") == "0" * 32]


def test_telemetry_failure_does_not_fail_the_case(
    store: InMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("metrics backend down")

    monkeypatch.setattr(runtime_module, "record_case_run", boom)
    runtime, case_id = _run_low_risk_case(store)  # must not raise

    assert not any(e.tool == "workflow_failed" for e in store.audit)
    assert case_id not in runtime._active


@pytest.fixture
def toolbox(
    store: InMemoryStore,
    txn_server: AuditedMCPServer,
    merchant_server: AuditedMCPServer,
    cases_server: AuditedMCPServer,
) -> ToolBox:
    return ToolBox(
        agent="evidence_agent",
        allowed=frozenset({"get_txn"}),
        tools=tool_index([txn_server, merchant_server, cases_server]),
        audit=store,
        timeout=5,
        retries=0,
    )


def test_guardrail_denial_is_not_an_error_span(toolbox: ToolBox) -> None:
    with pytest.raises(ToolDeniedError):
        toolbox.call("save_draft", {"case_id": "case_0001"})
    span = _by_name(_SPANS.get_finished_spans(), "tool.save_draft")[0]

    assert span.status.status_code != StatusCode.ERROR
    assert span.attributes is not None and span.attributes["outcome"] == "denied"
    assert not [e for e in span.events if e.name == "exception"]


def test_error_spans_carry_the_exception_type_not_its_message(toolbox: ToolBox) -> None:
    with pytest.raises(ToolCallError):
        toolbox.call("get_txn", {"txn_id": "txn_999999"})
    span = _by_name(_SPANS.get_finished_spans(), "tool.get_txn")[0]

    assert span.status.status_code == StatusCode.ERROR
    assert span.status.description == "ToolCallError"
    # no exception event, so no message text leaves the process
    assert not [e for e in span.events if e.name == "exception"]
