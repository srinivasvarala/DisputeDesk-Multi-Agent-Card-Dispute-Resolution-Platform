"""OpenTelemetry setup, the span helpers the app uses, and the metrics.

Everything here uses the OTel API, so with no SDK installed (unit tests, plain runs, `OTEL_ENABLED`
unset) it is a no-op. `setup_telemetry` installs the SDK and exporters when enabled.

Attributes on spans: `case_id`, `agent`, `risk_tier`, `guardrail.triggered`.
Metrics (names as exported to Prometheus get `_` instead of `.`):
- `agent.latency` (ms histogram, label `agent`)
- `case.cost_usd` (histogram, one point per run of a case; `case_id` is on the span, not a label)
- `guardrail.checks` (counter, labels `rail`, `triggered`; rate = triggered / all)
- `case.revision_loops` (histogram of reviewer-requested revisions per run)
"""

from __future__ import annotations

import contextvars
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.trace import Span, SpanContext, Status, StatusCode

from disputedesk.config import Settings

logger = logging.getLogger(__name__)

_TRACER = "disputedesk"
_installed = False


# --------------------------------------------------------------------------- per-case context


@dataclass
class CaseTotals:
    """Mutable, shared by reference across copied contexts: LLM calls add their cost here."""

    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0


@dataclass(frozen=True)
class CaseContext:
    case_id: str
    agent: str | None = None
    risk_tier: str | None = None
    totals: CaseTotals | None = None


_case: contextvars.ContextVar[CaseContext | None] = contextvars.ContextVar("case", default=None)


def current_case() -> CaseContext | None:
    return _case.get()


def run_in_context[T](fn: Callable[[], T]) -> Callable[[], T]:
    """Wrap `fn` so a worker thread runs with the caller's trace and case context.

    `ThreadPoolExecutor.submit` does not copy contextvars, which would orphan the spans made inside.
    """
    ctx = contextvars.copy_context()
    return lambda: ctx.run(fn)


# --------------------------------------------------------------------------- metrics


def _meter() -> metrics.Meter:
    return metrics.get_meter(_TRACER)


def record_agent_latency(agent: str, millis: float) -> None:
    _meter().create_histogram("agent.latency", unit="ms").record(millis, {"agent": agent})


def record_guardrail_check(rail: str, triggered: bool) -> None:
    _meter().create_counter("guardrail.checks").add(1, {"rail": rail, "triggered": triggered})


def record_case_run(totals: CaseTotals, revisions: int, risk_tier: str | None) -> None:
    attrs = {"risk_tier": risk_tier or "unknown"}
    _meter().create_histogram("case.cost_usd", unit="USD").record(totals.cost_usd, attrs)
    _meter().create_histogram("case.revision_loops").record(revisions, attrs)


# --------------------------------------------------------------------------- spans


def _tracer() -> trace.Tracer:
    return trace.get_tracer(_TRACER)


@contextmanager
def span_scope(name: str, attributes: dict[str, Any]) -> Iterator[Span]:
    """Start a span without exporting exception messages (they can echo merchant or customer
    text). An error is recorded as its type name only; a `PermissionError` (a guardrail denial,
    which is the guardrail working) is not an error at all."""
    with _tracer().start_as_current_span(
        name, attributes=attributes, record_exception=False, set_status_on_exception=False
    ) as span:
        try:
            yield span
        except PermissionError:
            span.set_attribute("outcome", "denied")
            raise
        except Exception as exc:
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            span.set_attribute("error.type", type(exc).__name__)
            raise


@contextmanager
def case_span(
    name: str,
    case_id: str,
    *,
    parent: trace.SpanContext | None = None,
    totals: CaseTotals | None = None,
) -> Iterator[tuple[Span, CaseTotals]]:
    """Root span of one run of a case (`case.run` / `case.resume`).

    With `parent`, the span joins that earlier trace, so a case paused for approval and resumed
    later is still one trace. Pass the same `totals` to every run of a case so cost and call
    counts add up across a pause.
    """
    ctx = None
    if parent is not None and parent.is_valid:
        ctx = trace.set_span_in_context(trace.NonRecordingSpan(parent))
    totals = totals or CaseTotals()
    token = _case.set(CaseContext(case_id=case_id, totals=totals))
    try:
        with _tracer().start_as_current_span(
            name, context=ctx, attributes={"case_id": case_id}
        ) as span:
            yield span, totals
            span.set_attribute("case.cost_usd", totals.cost_usd)
    finally:
        _case.reset(token)


@contextmanager
def agent_span(agent: str, *, risk_tier: str | None = None) -> Iterator[Span]:
    """One span per graph node. Sets `agent` (and `risk_tier` once known) on the case context."""
    base = _case.get() or CaseContext(case_id="")
    tier = risk_tier or base.risk_tier
    token = _case.set(CaseContext(base.case_id, agent, tier, base.totals))
    attrs: dict[str, str] = {"agent": agent}
    if base.case_id:
        attrs["case_id"] = base.case_id
    if tier:
        attrs["risk_tier"] = tier
    started = time.monotonic()
    try:
        with span_scope(f"agent.{agent}", attrs) as span:
            yield span
    finally:
        record_agent_latency(agent, (time.monotonic() - started) * 1000)
        _case.reset(token)


def set_risk_tier(span: Span, risk_tier: str) -> None:
    """The router decided the tier: stamp it on the node span and on the rest of the run."""
    span.set_attribute("risk_tier", risk_tier)
    base = _case.get()
    if base is not None:
        _case.set(CaseContext(base.case_id, base.agent, risk_tier, base.totals))


@contextmanager
def tool_span(tool: str, agent: str, case_id: str | None) -> Iterator[Span]:
    attrs: dict[str, str] = {"agent": agent, "tool": tool}
    if case_id:
        attrs["case_id"] = case_id
    with span_scope(f"tool.{tool}", attrs) as span:
        yield span


def trace_ids() -> tuple[str, str] | None:
    """(trace_id, span_id) of the current span as hex, or None when there is none."""
    ctx: SpanContext = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return None
    return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")


# --------------------------------------------------------------------------- SDK setup


def setup_telemetry(settings: Settings) -> bool:
    """Install the OTel SDK with OTLP/HTTP exporters. Returns False (and does nothing) unless
    `OTEL_ENABLED` is set, so tests and CI never try to reach a collector."""
    global _installed
    if not settings.otel_enabled or _installed:
        return _installed
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/")
    resource = Resource.create({"service.name": settings.otel_service_name})
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"))
    )
    trace.set_tracer_provider(tracer_provider)
    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics"), export_interval_millis=5000
    )
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
    PsycopgInstrumentor().instrument()  # every query becomes a span; call before connecting
    _installed = True
    logger.info("telemetry enabled, exporting to %s", endpoint)
    return True


def instrument_app(app: Any) -> None:
    """Server spans for every request (no-op when telemetry is off)."""
    if not _installed:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)
