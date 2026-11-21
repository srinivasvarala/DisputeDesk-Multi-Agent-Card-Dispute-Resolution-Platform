"""Builds the dispute graph from a set of node functions.

    intake -> router -> evidence -> policy -> resolution -> reviewer
                                                  ^            |
                                                  +- revise ---+  (at most MAX_REVISIONS)
    reviewer approve -> output_check (PII and grounding of the letter)
        -> finalize                                     (low risk)
        -> request_approval -> human_gate               (approval required) -> finalize
    revisions exhausted, an escalate draft, a blocked letter, or a human "no" -> escalate
    intake blocked the narrative (input guardrail)          -> escalate, no agent reads it

Nodes are injected (`Nodes`) so the skeleton can run on stubs and tests can swap any one node.
`human_gate` is the only place that pauses: it calls `interrupt()`, so the run stops at a
checkpoint and continues when the caller resumes the thread with `Command(resume=...)`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from disputedesk.graph.routing import (
    after_human,
    after_intake,
    after_output_check,
    after_resolution,
    after_review,
)
from disputedesk.graph.state import DisputeState, HumanDecision
from disputedesk.observability.otel import agent_span, set_risk_tier

Update = dict[str, Any]


class Node(Protocol):
    """A graph node: takes the state, returns only the fields it changes."""

    def __call__(self, state: DisputeState) -> Update: ...


# Order matters only for readability; edges below define the flow.
NODE_NAMES = (
    "intake",
    "router",
    "evidence",
    "policy",
    "resolution",
    "reviewer",
    "output_check",
    "request_approval",
    "finalize",
    "escalate",
)


@dataclass(frozen=True)
class Nodes:
    intake: Node
    router: Node
    evidence: Node
    policy: Node
    resolution: Node
    reviewer: Node
    output_check: Node
    request_approval: Node
    finalize: Node
    escalate: Node


def human_gate(state: DisputeState) -> Update:
    """Pause until a person decides. Re-runs from the top on resume, so keep it side-effect free."""
    answer = interrupt(
        {
            "case_id": state.case_id,
            "draft": state.draft.model_dump(mode="json") if state.draft else None,
            "risk_tier": state.route.risk_tier if state.route else None,
        }
    )
    return {"human": HumanDecision.model_validate(answer), "path": [*state.path, "human_gate"]}


def _tracked(name: str, node: Node) -> Node:
    """Record the visit in `path` so tests and the API can show the route a case took."""

    def run(state: DisputeState) -> Update:
        tier = str(state.route.risk_tier) if state.route else None  # from checkpointed state
        with agent_span(name, risk_tier=tier) as span:
            update = node(state)
            route = update.get("route")
            if route is not None:
                set_risk_tier(span, str(route.risk_tier))
            if update.get("review") is not None:
                span.set_attribute("review.verdict", str(update["review"].verdict))
        return {**update, "path": [*state.path, name]}

    return run


def build_graph(
    nodes: Nodes, checkpointer: BaseCheckpointSaver[Any] | None
) -> CompiledStateGraph[DisputeState, None, DisputeState, DisputeState]:
    graph = StateGraph(DisputeState)
    for name in NODE_NAMES:
        graph.add_node(name, _tracked(name, getattr(nodes, name)))
    graph.add_node("human_gate", human_gate)

    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", after_intake, ["router", "escalate"])
    graph.add_edge("router", "evidence")
    graph.add_edge("evidence", "policy")
    graph.add_edge("policy", "resolution")
    graph.add_conditional_edges("resolution", after_resolution, ["reviewer", "escalate"])
    graph.add_conditional_edges(
        "reviewer", after_review, ["resolution", "output_check", "escalate"]
    )
    graph.add_conditional_edges(
        "output_check", after_output_check, ["request_approval", "finalize", "escalate"]
    )
    graph.add_edge("request_approval", "human_gate")
    graph.add_conditional_edges("human_gate", after_human, ["finalize", "escalate"])
    graph.add_edge("finalize", END)
    graph.add_edge("escalate", END)
    return graph.compile(checkpointer=checkpointer)
