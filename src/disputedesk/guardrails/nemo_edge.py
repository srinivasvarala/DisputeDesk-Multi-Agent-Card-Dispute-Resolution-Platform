"""NeMo Guardrails at the edges: input rails before text reaches an agent, output rails before
text leaves. Nothing else goes through NeMo; LangGraph owns the flow.

Version note (NeMo 0.25.0): `LLMRails` is deprecated in favour of `Guardrails`, but the new
IORails engine only supports catalogued rails, not our custom Colang flows. We use
`Guardrails(..., use_iorails=False)`, which runs the legacy engine without the deprecation
warning. Revisit before NeMo 0.27, when LLMRails may be removed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from nemoguardrails import Guardrails, RailsConfig
from nemoguardrails.rails.llm.options import RailStatus
from pydantic import BaseModel

from disputedesk.guardrails.judges import (
    LLMJudge,
    looks_like_jailbreak,
    looks_off_topic,
    ungrounded_figures,
)
from disputedesk.guardrails.redaction import Redactor

CONFIG_DIR = Path(__file__).parent / "nemo_config"

# Grounding context for the output rail currently being checked (set per call, per task).
_grounding: ContextVar[Sequence[str] | None] = ContextVar("grounding", default=None)


class EdgeResult(BaseModel):
    blocked: bool
    rail: str | None = None


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine from sync code, even when a loop is already running in this thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


class NemoEdge:
    def __init__(self, redactor: Redactor, judge: LLMJudge | None = None) -> None:
        self._redactor = redactor
        self._judge = judge
        self._rails = Guardrails(RailsConfig.from_path(str(CONFIG_DIR)), use_iorails=False)
        self._rails.register_action(self._detect_jailbreak, "detect_jailbreak")
        self._rails.register_action(self._detect_off_topic, "detect_off_topic")
        self._rails.register_action(self._detect_pii, "detect_pii")
        self._rails.register_action(self._detect_ungrounded, "detect_ungrounded")

    # --- actions (NeMo calls these from the Colang flows) ---

    async def _detect_jailbreak(self, text: str) -> bool:
        if looks_like_jailbreak(text):
            return True
        # The judge makes a blocking LLM call: keep it off the event loop.
        return self._judge is not None and await asyncio.to_thread(self._judge.is_jailbreak, text)

    async def _detect_off_topic(self, text: str) -> bool:
        # Heuristic only: the judge is asked about manipulation, not topic. A second model call
        # per input for a rail this cheap would add cost and false positives.
        return looks_off_topic(text)

    async def _detect_pii(self, text: str) -> bool:
        return bool(self._redactor.find(text))

    async def _detect_ungrounded(self, text: str) -> bool:
        context = _grounding.get()
        if context is None:  # no grounding material supplied: the rail has nothing to check
            return False
        return bool(ungrounded_figures(text, context))

    # --- edges ---

    def check_input(self, text: str) -> EdgeResult:
        return _run(self._check([{"role": "user", "content": text}]))

    def check_output(self, text: str, grounding: Sequence[str] | None = None) -> EdgeResult:
        messages = [
            {"role": "user", "content": "(case context)"},
            {"role": "assistant", "content": text},
        ]
        return _run(self._check(messages, grounding))

    async def _check(
        self, messages: list[dict[str, str]], grounding: Sequence[str] | None = None
    ) -> EdgeResult:
        token = _grounding.set(grounding)
        try:
            result = await self._rails.check_async(messages)
        finally:
            _grounding.reset(token)
        if result.status == RailStatus.PASSED:
            return EdgeResult(blocked=False)
        return EdgeResult(blocked=True, rail=result.rail)
