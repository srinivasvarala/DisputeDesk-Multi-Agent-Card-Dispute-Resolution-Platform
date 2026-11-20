"""`TracedLLM`: wraps any `LLMClient` so each call is (1) an OTel span and (2) a Langfuse
generation (prompt, response, tokens, cost).

Why two backends: the self-hosted Langfuse v2 has no OTLP ingest (ADR-001), so spans go to the
OTel collector and generations go through the Langfuse SDK. They share one id: the Langfuse trace
id is the OTel trace id, so a trace found in Jaeger is found by the same id in Langfuse.

Prompts and replies are redacted before they leave the process. Langfuse is optional: with no
keys set, only the OTel span is produced. Telemetry never breaks an LLM call.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from disputedesk.config import Settings
from disputedesk.guardrails.redaction import get_redactor
from disputedesk.llm import LLMClient, LLMResponse, Message
from disputedesk.observability.otel import current_case, span_scope, trace_ids

logger = logging.getLogger(__name__)


def llm_cost_usd(settings: Settings, input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens * settings.llm_price_input_per_mtok
        + output_tokens * settings.llm_price_output_per_mtok
    ) / 1_000_000


def make_langfuse(settings: Settings) -> Any | None:
    """A Langfuse client, or None when keys are not configured."""
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    from langfuse import Langfuse

    return Langfuse(
        public_key=settings.langfuse_public_key.get_secret_value(),
        secret_key=settings.langfuse_secret_key.get_secret_value(),
        host=settings.langfuse_host,
    )


def update_trace(
    langfuse: Any | None,
    trace_id: str,
    *,
    case_id: str,
    input: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
) -> None:
    """Set the trace-level Input / Output shown at the top of a Langfuse trace.

    Callers pass facts about the run (ids, decision, path, cost), never customer-written text:
    prompts and replies live on the generations, redacted.
    """
    if langfuse is None:
        return
    try:
        langfuse.trace(id=trace_id, session_id=case_id, input=input, output=output)
    except Exception:
        logger.warning("langfuse trace update failed", exc_info=True)


class TracedLLM:
    def __init__(
        self,
        inner: LLMClient,
        settings: Settings,
        langfuse: Any | None = None,
        redact: Callable[[str], str] | None = None,
    ) -> None:
        self._inner = inner
        self._settings = settings
        self._langfuse = langfuse
        self._redact = redact or (lambda text: get_redactor().redact(text)[0])
        self.provider = inner.provider

    def complete(
        self, *, system: str, messages: Sequence[Message], max_tokens: int = 4096
    ) -> LLMResponse:
        case = current_case()
        agent = case.agent if case and case.agent else "unknown"
        start = datetime.now(UTC)
        with span_scope(f"llm.{agent}", {"agent": agent, "llm.provider": self.provider}) as span:
            if case and case.case_id:
                span.set_attribute("case_id", case.case_id)
            if case and case.risk_tier:
                span.set_attribute("risk_tier", case.risk_tier)
            started = time.monotonic()
            reply = self._inner.complete(system=system, messages=messages, max_tokens=max_tokens)
            cost = llm_cost_usd(self._settings, reply.input_tokens, reply.output_tokens)
            span.set_attribute("llm.model", reply.model)
            span.set_attribute("llm.input_tokens", reply.input_tokens)
            span.set_attribute("llm.output_tokens", reply.output_tokens)
            span.set_attribute("llm.cost_usd", cost)
            if case and case.totals:
                case.totals.cost_usd += cost
                case.totals.input_tokens += reply.input_tokens
                case.totals.output_tokens += reply.output_tokens
                case.totals.llm_calls += 1
            self._send(agent, system, messages, reply, cost, start, time.monotonic() - started)
        return reply

    def _send(
        self,
        agent: str,
        system: str,
        messages: Sequence[Message],
        reply: LLMResponse,
        cost: float,
        start: datetime,
        seconds: float,
    ) -> None:
        ids = trace_ids()
        if self._langfuse is None or ids is None:
            return
        trace_id, span_id = ids  # span id is on the OTel side; Langfuse has no such parent
        case = current_case()
        try:
            self._langfuse.trace(
                id=trace_id,
                name=f"dispute {case.case_id}" if case and case.case_id else "dispute",
                session_id=case.case_id if case and case.case_id else None,
                tags=[case.risk_tier] if case and case.risk_tier else None,
                metadata={"case_id": case.case_id if case else None},
            )
            self._langfuse.generation(
                trace_id=trace_id,
                name=agent,
                model=reply.model,
                input=[
                    {"role": "system", "content": self._redact(system)},
                    *({"role": m.role, "content": self._redact(m.content)} for m in messages),
                ],
                output=self._redact(reply.text),
                usage={
                    "input": reply.input_tokens,
                    "output": reply.output_tokens,
                    "unit": "TOKENS",
                    "total_cost": cost,
                },
                start_time=start,
                end_time=datetime.fromtimestamp(start.timestamp() + seconds, UTC),
                metadata={"agent": agent, "provider": reply.provider, "otel_span_id": span_id},
            )
        except Exception:
            logger.warning("langfuse export failed", exc_info=True)
