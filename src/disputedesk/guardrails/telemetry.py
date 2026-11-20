"""Guardrail events as trace attributes (and a span event) on the current OpenTelemetry span.

Uses only the OTel API: with no SDK configured (unit tests, plain runs) it is a no-op.
M6 installs the SDK and exporter, and these attributes show up in the traces.
"""

from __future__ import annotations

from opentelemetry import trace


def emit_guardrail_event(
    *, rail: str, action: str, agent: str, case_id: str | None, triggered: bool, reason: str = ""
) -> None:
    span = trace.get_current_span()
    attributes: dict[str, str | bool] = {
        "guardrail.rail": rail,
        "guardrail.action": action,
        "guardrail.agent": agent,
        "guardrail.triggered": triggered,
    }
    if case_id:
        attributes["case_id"] = case_id
    if reason:
        attributes["guardrail.reason"] = reason
    # The span-level flag is sticky: one triggered rail marks the whole span.
    if triggered:
        span.set_attribute("guardrail.triggered", True)
    span.add_event("guardrail", attributes)
