# ADR-002: LangGraph for orchestration

- Status: accepted
- Date: 2026-11-19

## Context

A dispute moves through fixed steps (route, gather evidence, look up policy, draft, review), may loop back
for a revision, and for risky cases must **stop for a human and resume later**, possibly after a restart.
The control flow has to be inspectable and testable, and the model must not be able to skip a step.

## Decision

Use LangGraph with a Pydantic `DisputeState`, a Postgres checkpointer and `interrupt()` for the human gate.

Alternatives considered:

- **Plain Python functions.** Simplest, but pause/resume across a restart means writing our own state
  persistence, which is most of what LangGraph gives us.
- **An agent loop where the model picks the next step** (ReAct style). Flexible, but the order of steps and
  the approval requirement would then be up to the model. Here they are rules.
- **CrewAI / AutoGen.** Role-play abstractions with less control over the exact flow and checkpointing.
- **Temporal or Step Functions.** Better durability at scale, but heavy for a laptop demo. Step Functions is
  a reasonable M9 revisit.

## Consequences

- The graph is built from injected nodes, so the skeleton was tested on stubs first and real agents replaced
  them one at a time. Routing is pure functions in `graph/routing.py`.
- Risk tier and approval are plain Python (`graph/rules.py`); the graph, not a model, sends high-risk cases to a human.
- Approval is its own node, separate from the gate, because LangGraph re-runs an interrupted node from the top on resume.
- Completion is read from `final_status`, not from an empty `next`: LangGraph can checkpoint between nodes
  with no `next` yet, which briefly showed `completed` in a live run.
- The checkpoint serializer needs an explicit type allowlist (`graph/serde.py`).
- We depend on LangGraph's checkpoint format. After a restart mid-run a case keeps its checkpoint but nothing drives it.
