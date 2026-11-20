"""Citation faithfulness with DeepEval: is the Policy agent's summary supported by the rulebook
sections it cites?

`input` is a fixed question, `actual_output` is the summary, and `retrieval_context` is the full
text of the cited sections (not just the short quotes: a summary may fairly rely on the rest of a
section the quote came from). DeepEval extracts claims from the summary and checks each against
that context. Its judge model is ours (`JudgeModel` wraps an `LLMClient`), so no provider SDK is
imported here and the judge is configurable with EVAL_JUDGE_*.

DeepEval is in the optional `evals` dependency group, so this module is imported lazily.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from disputedesk.llm import LLMClient, Message

# DeepEval sends usage telemetry by default. Synthetic data only, but opt out anyway.
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")

from deepeval.metrics import FaithfulnessMetric
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase

QUESTION = "What does the DisputeDesk Rulebook say about how to handle this dispute?"
SYSTEM = "You are a careful evaluator. When asked for JSON, reply with that JSON object only."


class JudgeModel(DeepEvalBaseLLM):  # type: ignore[no-untyped-call]
    def __init__(self, llm: LLMClient, name: str) -> None:
        self._llm = llm
        super().__init__(model=name)

    def load_model(self) -> JudgeModel:
        return self

    def get_model_name(self) -> str:
        return str(self.name)

    def generate(self, prompt: str, schema: type[BaseModel] | None = None) -> Any:
        reply = self._llm.complete(
            system=SYSTEM, messages=[Message(role="user", content=prompt)], max_tokens=2048
        )
        if schema is None:
            return reply.text
        start, end = reply.text.find("{"), reply.text.rfind("}")
        if start == -1 or end < start:
            raise ValueError("judge did not return a JSON object")
        return schema.model_validate(json.loads(reply.text[start : end + 1]))

    async def a_generate(self, prompt: str, schema: type[BaseModel] | None = None) -> Any:
        return await asyncio.to_thread(self.generate, prompt, schema)


class Faithfulness(BaseModel):
    score: float | None
    error: str | None = None


def score_faithfulness(
    llm: LLMClient, model_name: str, summary: str, context: Sequence[str]
) -> Faithfulness:
    metric = FaithfulnessMetric(
        threshold=0.0,
        model=JudgeModel(llm, model_name),
        include_reason=False,
        async_mode=False,
        verbose_mode=False,
    )
    case = LLMTestCase(input=QUESTION, actual_output=summary, retrieval_context=list(context))
    try:
        metric.measure(case, _show_indicator=False)
    except Exception as exc:  # a judge failure is a missing score, never a fake one
        return Faithfulness(score=None, error=f"{type(exc).__name__}: {exc}")
    return Faithfulness(score=None if metric.score is None else float(metric.score))
