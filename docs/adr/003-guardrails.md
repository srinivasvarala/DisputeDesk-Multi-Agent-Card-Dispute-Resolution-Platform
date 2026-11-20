# ADR-003: Guardrails behind one interface (rules, NeMo, Presidio)

- Status: accepted
- Date: 2026-11-19

## Context

Three kinds of check are needed: hard limits that must never depend on a model (which agent may call which
tool, the refund cap), content checks on text going in and out (jailbreaks, off-topic, ungrounded amounts),
and PII redaction. The project rule is that all of them are called through one place.

## Decision

`guardrails/interface.py` exposes `check_input`, `check_output` and `check_tool_call`, each returning a `Verdict`
(`allow | redact | block | deny | require_approval`). Behind it:

- **Plain Python** (`policy_rules.py`) for allowlists and the refund cap. Deny wins over the cap. No LLM, no NeMo.
- **NeMo Guardrails** for input and output rails, used only at the edges. LangGraph owns the flow. Rails are
  Python actions behind Colang flows, with no NeMo-managed LLM, so "all LLM calls go through `llm.py`" still holds.
- **Presidio** for redaction of cards, SSNs, emails, phones and IBANs. PERSON is excluded: it flagged too many merchant names.
- Any check that errors **fails closed**.

## Consequences

- Swapping NeMo for Bedrock Guardrails in M9 means a new implementation behind the same three functions.
- `LLMRails` is deprecated in NeMo 0.25; we pass `use_iorails=False` because the replacement engine only supports
  catalogued rails, not custom Colang flows. Revisit on upgrade.
- The regex heuristics are a baseline: they catch 3 of 9 attacks in the evals.
- **Only `check_tool_call` is wired into the graph.** `check_input` and `check_output` are built and tested
  but not yet called at intake and finalize, so the guardrail catch rate (0.333) fails its threshold. Wiring
  them in is the first item under "what I'd do next".
