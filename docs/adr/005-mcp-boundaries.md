# ADR-005: MCP server boundaries and agent credentials

- Status: accepted
- Date: 2026-11-19

## Context

Agents need data and the ability to change case state. The risk is an agent, or text steering it, calling a
tool it should not have, for example the evidence agent changing a case status. Every call also has to be audited
with the right actor.

## Decision

Three MCP servers split by what they touch:

- `transactions` (`get_txn`, `list_txns`, `get_history`): transaction and card history (read-only).
- `merchant` (`get_profile`, `get_dispute_rate`, `get_delivery_status`): merchant risk and delivery proof (read-only).
- `cases` (`get_case`, `save_draft`, `update_status`): the case, drafts and status changes (the only server that writes).

Each agent has an explicit allowlist in one file (`agents/allowlists.py`); the reviewer has no tools at all.
Status changes belong to the non-LLM `dispute_workflow` step, so no agent can resolve a case. The `ToolBox`
refuses a call outside the allowlist, then mints a 60-second signed credential listing the agent's tools. Each
server verifies it and denies anything else, even if the `ToolBox` is bypassed. The audit row records the
verified agent, not a name the caller claims.

## Consequences

- Least privilege is checked in three places (toolbox, guardrail rules, server), and the first test run already
  caught a tool missing from the evidence agent's allowlist.
- Tools run in-process through a real MCP client named after the agent, so we get the real protocol and audit
  rows without subprocesses. Each server also has a standalone entry point (`python -m disputedesk.mcp_servers.<name>`).
  A networked client can replace the in-process one behind the same `ToolBox` interface.
- The credential is minted in the same process with the same HS256 secret as user tokens (separated by
  audience). It guards against bugs and bypasses, not a compromised API process. Cognito or per-service keys fix that in M9.
- Merchant- and customer-derived tool output is still untrusted text; it is wrapped before it reaches a prompt.
