# ADR-003: Guardrails behind one interface (rules, NeMo, Presidio)

- Status: accepted
- Date: 2026-11-19

## Context

Three kinds of check are needed: hard limits that must never depend on a model (which agent may call which
tool), content checks on text going in and out (jailbreaks, off-topic, ungrounded amounts),
and PII redaction. The project rule is that all of them are called through one place.

## Decision

`guardrails/interface.py` exposes `check_input`, `check_output` and `check_tool_call`, each returning a `Verdict`
(`allow | redact | block | deny`). Behind it:

- **Plain Python** (`policy_rules.py`) for the per-agent tool allowlist. No LLM, no NeMo.
- **NeMo Guardrails** for input and output rails, used only at the edges. LangGraph owns the flow. Rails are
  Python actions behind Colang flows, with no NeMo-managed LLM, so "all LLM calls go through `llm.py`" still holds.
- **Presidio** for redaction of cards, SSNs, emails, phones and IBANs. PERSON is excluded: it flagged too many merchant names.
- Any check that errors **fails closed**.

## Consequences

- Swapping NeMo for Bedrock Guardrails in M9 means a new implementation behind the same three functions.
- `LLMRails` is deprecated in NeMo 0.25; we pass `use_iorails=False` because the replacement engine only supports
  catalogued rails, not custom Colang flows. Revisit on upgrade.
- The regex heuristics are a baseline: they catch 3 of 9 attacks in the evals.
- **All three checks are wired into the graph.** The input check runs at intake (M7.4): a block escalates
  the case to a human with no agent call, a redaction means agents only see the clean text. The output check
  runs in the `output_check` step (M10), after the reviewer approves and before finalize or the human gate:
  PII is redacted into a new draft version, a block or a guardrail error escalates the case.
- **The refund cap was removed (M10).** It originally forced human approval for a refund at or above the
  approval threshold, but no tool carries a refund amount (nothing is paid out), so it could never fire. The
  risk tier in `graph/rules.py` is what sends large amounts to a human. `check_tool_call` keeps its
  `arguments` parameter as the place for an argument rule if a tool ever needs one. `REQUIRE_APPROVAL`,
  `ApprovalRequiredError` and the `approval_required` audit outcome went with it.
- The first input detectors caught 3 of 9 eval attacks. They were rebuilt as generic patterns grouped by what
  the attacker is trying to do, tuned on a separate held-out dev set, with the LLM judge as an opt-in second
  opinion (`GUARDRAIL_JUDGE`; asked about manipulation only, never about topic; fails closed). The judge's
  token budget matters: a 64-token cap would truncate every Haiku reply and fail closed on everything.
- Because a rail that stops attacks hides whether the layers below it hold, the evals also run the adversarial
  cases with the input rail off.
