"""The one place that says which tools each agent may use. Least privilege: no agent gets a
tool it does not need. `get_case`/`update_status` belong to the system steps, not the LLM agents.
"""

from __future__ import annotations

SYSTEM = "dispute_workflow"  # intake, approval request, finalize and escalate steps (no LLM)
ROUTER = "router_agent"
EVIDENCE = "evidence_agent"
POLICY = "policy_agent"
RESOLUTION = "resolution_agent"
REVIEWER = "reviewer_agent"
OUTPUT_GUARD = "output_guard"  # redacts a letter and saves it as a new draft version (no LLM)

ALLOWLISTS: dict[str, frozenset[str]] = {
    SYSTEM: frozenset({"get_case", "update_status"}),
    ROUTER: frozenset({"get_txn", "get_profile"}),
    EVIDENCE: frozenset(
        {
            "get_txn",
            "list_txns",
            "get_history",
            "get_profile",
            "get_dispute_rate",
            "get_delivery_status",
        }
    ),
    POLICY: frozenset({"search_policies"}),  # the RAG retriever, not an MCP tool
    RESOLUTION: frozenset({"save_draft"}),
    REVIEWER: frozenset(),  # judges the draft from state; no tools at all
    OUTPUT_GUARD: frozenset({"save_draft"}),  # only to store the redacted letter
}
