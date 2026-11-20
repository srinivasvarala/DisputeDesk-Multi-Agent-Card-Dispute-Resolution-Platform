"""Shared by the unit and integration tests that drive the whole graph."""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterable, Iterator, Sequence

from fastapi.testclient import TestClient

from disputedesk.agents.fake_llm import agent_of, demo_responder
from disputedesk.api.app import create_app
from disputedesk.api.runtime import Runtime
from disputedesk.config import Settings
from disputedesk.db.models import Case, ReasonCode
from disputedesk.graph import rules
from disputedesk.llm import FakeClient, Message
from disputedesk.mcp_servers.common.store import InMemoryStore
from disputedesk.rag.retriever import RetrievedChunk
from disputedesk.security.jwt import issue_token
from disputedesk.security.scopes import Role

APPROVE = json.dumps({"verdict": "approve", "feedback": ""})
REVISE = json.dumps({"verdict": "revise", "feedback": "Tighten the letter and cite the rule."})


class StubRetriever:
    """Two fixed policy excerpts, so graph tests do not need the RAG index."""

    def search(self, query: str, *, k: int = 5) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(
                source="11-approval-thresholds-and-escalation.md",
                section="Automated decision limit",
                doc_title="Approval Thresholds and Escalation",
                chunk_index=0,
                content="An automated analyst may finalise a decision only when the "
                "disputed amount is below $500.00.",
                score=1.0,
                rrf_score=0.03,
            )
        ][:k]


def fake_llm(reviews: Iterable[str] = (), *, then: str = APPROVE) -> tuple[FakeClient, list[str]]:
    """The demo fake model, except that Reviewer replies come from `reviews` first, then
    `then` forever. Pass `itertools.repeat(REVISE)` to make the reviewer never satisfied.
    Returns the client and a log of which agents were called, in order."""
    queue: Iterator[str] = iter(reviews)
    calls: list[str] = []

    def respond(system: str, messages: Sequence[Message]) -> str:
        agent = agent_of(system)
        calls.append(agent)
        if agent == "reviewer":
            return next(queue, then)
        return demo_responder(system, messages)

    return FakeClient(respond), calls


def always_revise() -> Iterable[str]:
    return itertools.repeat(REVISE)


def needs_approval(case: Case, store: InMemoryStore, settings: Settings) -> bool:
    """Whether the router's rules will require a human for this case (fake router answers
    with the claimed reason)."""
    txn = store.transactions[case.txn_id]
    tier = rules.risk_tier(
        amount_cents=case.amount_cents,
        merchant_risk=store.merchants[txn.merchant_id].risk_tier,
        reason=ReasonCode(case.reason_code),
        threshold_cents=settings.human_approval_threshold_cents,
    )
    return rules.approval_required(tier)


TEST_JWT_SECRET = "test-secret-" + "x" * 32


def auth_headers(
    settings: Settings, *, sub: str = "ana", role: Role = Role.APPROVER
) -> dict[str, str]:
    """A bearer header for `sub` with `role`. Default is an approver, who can do everything the
    workflow tests need."""
    return {"Authorization": f"Bearer {issue_token(settings, sub=sub, role=role)}"}


def authed_client(runtime: Runtime, settings: Settings, **kwargs: object) -> TestClient:
    client = TestClient(create_app(runtime, settings))
    client.headers.update(auth_headers(settings, **kwargs))  # type: ignore[arg-type]
    return client
