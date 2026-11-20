"""Promptfoo provider: runs one synthetic dispute through the real DisputeDesk graph.

Each test supplies a `base` golden case (a clean dispute whose correct outcome is known) and a
`payload` that is appended to the cardholder narrative, or, when `field` is "description", put in
the merchant-supplied charge description. The provider returns the system's decision as JSON for
Promptfoo to assert on. It needs the same things as evals/run_evals.py: a real LLM and a Postgres
with the rulebook ingested.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr

from disputedesk.api.runtime import build_runtime
from disputedesk.config import Settings, get_settings
from disputedesk.db import connect
from disputedesk.evals.golden import build_world, load_golden
from disputedesk.evals.runner import run_case
from disputedesk.graph.serde import checkpoint_serde
from disputedesk.llm import LLMClient, get_embedder, get_llm, get_reranker
from disputedesk.rag.retriever import Retriever


@lru_cache(maxsize=1)
def _shared() -> tuple[Settings, LLMClient, Retriever]:
    settings = get_settings()
    if settings.jwt_secret is None:
        settings = settings.model_copy(update={"jwt_secret": SecretStr(secrets.token_hex(32))})
    conn = connect(settings.database_url)  # lives as long as the provider process
    retriever = Retriever(conn, get_embedder(settings), get_reranker(settings))
    return settings, get_llm(settings), retriever


def call_api(prompt: str, options: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    variables = context["vars"]
    settings, llm, retriever = _shared()
    base = {c.id: c for c in load_golden()}[variables["base"]]
    payload = variables["payload"]
    if variables.get("field") == "description":
        case = base.model_copy(update={"description": payload})
    else:
        case = base.model_copy(update={"narrative": f"{base.narrative} {payload}"})
    world = build_world([case], datetime.now(UTC))
    runtime = build_runtime(
        settings=settings,
        llm=llm,
        retriever=retriever,
        store=world.store,
        checkpointer=InMemorySaver(serde=checkpoint_serde()),
    )
    outcome = run_case(runtime, world, case)
    return {
        "output": json.dumps(
            {
                "workflow": outcome.workflow,
                "decision": outcome.decision,
                "reason_code": outcome.reason_code,
                "approval_required": outcome.approval_required,
                "rationale": outcome.rationale or "",
                "letter": outcome.letter or "",
                "error": outcome.error,
            }
        )
    }
