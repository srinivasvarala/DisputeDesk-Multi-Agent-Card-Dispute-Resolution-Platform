# Roadmap: phase 3 (M10 to M17)

M0 to M8 are done (see [PROGRESS.md](../PROGRESS.md)). This roadmap closes the gaps found in the
architecture and code review, and turns the project into one that can be shown to a hiring
manager. M9 (full AWS deployment) is **not** scheduled here: M16 delivers a smaller hosted demo
first, and M9 stays an optional follow-up.

> The data and the rulebook are synthetic. Portfolio quality, not production scale.

## How to read this

Each milestone is sized for **one working session** (about 3 to 4 hours, longer where an eval run
is waiting in the background). Every milestone has a goal, why it matters, requirements,
acceptance criteria that can be checked, and what is out of scope.

| # | Milestone | Closes | Needs an API key / spend | Size |
|---|---|---|---|---|
| M10 | Close the safety gaps ✅ done 2026-11-21 | Output guardrail unwired, audit text leak, dead rule, no maker-checker | No | Medium |
| M11 | Eval rigor | One run, same judge, no held-out set, no cost view | Yes, about $25 | Large |
| M12 | A tool-using evidence agent, with an ablation | "The agents are a fixed pipeline" | Yes, about $30 | Large |
| M13 | Cost-quality frontier | No model-choice evidence | Yes, cap $60 | Medium |
| M14 | Durable runs | In-memory run guard, lost runs on restart | No | Large |
| M15 | Tamper-evident audit log | Append-only but not tamper-evident | No | Small |
| M16 | Hosted demo | No clickable link | Small, capped | Large |
| M17 | Portfolio packaging | README length, no video, no interview notes | No (re-uses M11 to M13 results) | Medium |

**Dependencies.** M10 first. M11 before M12 and M13 (they need the repeat and split harness). M14
before M16 is preferred (a public demo should survive a restart), but not required. M17 last.

**If time runs short, keep this order:** M10, M11, M12, M16, M17. M13, M14 and M15 are the first
to cut. Do not cut M10 or M11: they fix credibility, not features.

### Rules for every milestone

These come from [CLAUDE.md](../CLAUDE.md) and the working agreements. They are not repeated in
each milestone.

1. **Plan first.** Show a short plan and wait for approval before writing code. Ask about any
   ambiguity listed under a milestone's "Decisions to confirm".
2. **One milestone per session.** Do not start the next one unless asked.
3. **After each logical step:** `uv run ruff check . && uv run ruff format .`, `uv run mypy src`,
   `uv run pytest tests/unit -q`, and the integration tests when the step touches Postgres. Fix
   before moving on. Commit after each green step with a conventional commit message.
4. **Existing conventions hold.** All LLM calls through `llm.py`, all guardrail checks through
   `guardrails/interface.py`, deterministic rules in plain Python, an explicit tool allowlist per
   agent, untrusted text only in user messages, every tool call and decision audited, no new
   dependency without saying why.
5. **Evals are reported honestly.** Never lower a threshold. A new threshold is committed
   **before** the run that measures it. Disclose every harness change made after seeing results.
   Discard an invalid run (outage, crash) and say so. Get approval for the spend before starting
   a paid run.
6. **At the end:** update PROGRESS.md (status table row, plus a section with delivered items,
   acceptance results, decisions, known gaps), and update README and `ARCHITECTURE.md` where the
   milestone changed what they say. Commit them with the milestone.
7. **Push only on an explicit yes**, one yes per push.

---

## M10: Close the safety gaps

**Goal.** Make the safety claims in the README true without footnotes: every letter passes the
output guardrail, the audit log cannot hold customer text, no guardrail rule is dead, and nobody
approves their own case.

**Why it matters.** `ARCHITECTURE.md` lists "`check_output` not wired" as the first gap. A
security-minded reviewer finds the other three in an hour. All four are cheap to fix and
protect the rest of the roadmap's credibility.

### Requirements

**R1. Wire `check_output` into the graph.**
- Runs on the letter of every `accept` and `reject` draft, after the reviewer approves and before
  `finalize` or `request_approval`. An `escalate` draft sends no letter, so it is not checked.
- Grounding material passed to the check: the evidence, the policy answer's text and the case
  facts, so figures in the letter can be checked against them.
- **PII found:** the letter is redacted and saved as a new draft version, then the flow continues.
- **Blocked (for example an ungrounded figure) or a guardrail error:** the case escalates with the
  rail name as the reason. No retry, no silent pass. Fail closed.
- The check is audited through the existing guardrail audit path. Audit rows hold rail names and
  entity types, never the text.
- The path appears in the case trace (`path` in state, the UI trace and a span attribute).

**R2. Audit rows never carry free text.**
- Agent failure rows (`router.py`, `evidence.py`, `resolution.py`, `policy.py`) currently write
  `error=str(exc)`. `AgentOutputError` embeds the pydantic validation message, which includes the
  model's reply, which can echo the cardholder's narrative. The log is append-only, so a leak is
  permanent.
- Record the exception **type** only, the same rule `Runtime._fail` already follows. Put the
  detail in the application log.
- Add a single helper so the rule lives in one place (`DecisionAuditor`), not in four call sites.
- Add a regression test with a canary string in the narrative that forces a validation failure,
  and assert the canary appears in no `audit_log` row and no trace attribute.

**R3. Resolve the dead refund-cap rule.**
- No tool carries `refund_cents` or `amount_cents`, so `REQUIRE_APPROVAL` from the refund cap can
  never fire. The graph's risk tier is the control that works.
- Default action: remove the refund-cap branch and its tests, keep the allowlist deny and the
  "unrecognised amount argument fails closed" behaviour if it still has a real target, and update
  the threat model and ADR-003 so they describe the system that exists.

**R4. Maker-checker on approval.**
- The user who started a case cannot approve it. Record the starter on the case (`started_by`
  column, added with the existing idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` pattern).
- `POST /disputes/{id}/approve` by the starter returns 403, is audited as `DENIED` with the reason
  "maker-checker", and leaves the case waiting. No admin exemption.
- The approver's note (already accepted, up to 1,000 characters) is stored in the approve audit
  row. It is human-written text, so it is length-capped and never copied into traces.
- The UI signs every request as one identity (`reviewer-ui`, role approver), so the maker and the
  checker would always be the same user. Add two demo personas to the UI (an analyst who starts,
  an approver who approves), selectable in the sidebar, still minting short-lived tokens.

### Out of scope
A real login system, token revocation, rate limiting (M16 adds a demo-level budget), or a payout.

### Outcome (2026-11-21)
Delivered; see [PROGRESS.md](../PROGRESS.md#m10-close-the-safety-gaps-). The four decisions below were
taken as recommended, with one change to the plan: the starter is stored in the graph state, not in
a `started_by` column on `cases`. The audit leak turned out wider than listed (reviewer, policy,
the MCP audit wrapper and the runtime span), and a further gap was found and left open: MCP audit
rows store tool arguments, including letters, as sent.

### Decisions to confirm in the plan
- R3: remove the refund-cap rule (default) or give it a real target?
- R4: store the note in the audit row (default), or in a separate `case_decisions` table?
- R1: should a grounding block escalate (default) or send the draft back to the resolution agent
  once?

### Acceptance criteria
- [ ] A graph test shows an `accept` letter containing an SSN-shaped string is saved redacted and
      the saved draft has no SSN. A letter with a figure that appears nowhere in the case escalates
      with the rail name as the reason.
- [ ] A test forces the output check to raise and the case escalates (fail closed).
- [ ] The golden-set unit validation still passes, and the fake-LLM end-to-end run still reaches
      `finalized` for a clean case.
- [ ] The canary test (R2) passes for router, evidence, policy and resolution failures.
- [ ] `grep -rn "error=str(exc)" src/disputedesk/agents` returns nothing.
- [ ] Starting a case as user A and approving as user A returns 403 and writes a `DENIED` audit
      row; approving as user B succeeds and the audit row holds the note.
- [ ] The UI demo works end to end with two personas.
- [ ] THREAT_MODEL.md has a row and a test reference for the output guardrail, the audit text
      rule and maker-checker. `ARCHITECTURE.md` section 12 no longer lists `check_output`.
- [ ] Ruff, mypy, unit and integration tests are green. PROGRESS.md updated.
- [ ] No claim is made here about real-model behaviour of the new output guardrail. The real-model
      red-team re-run happens in M11.

---

## M11: Eval rigor

**Goal.** Turn "one run on a set I have read" into numbers with error bars, a frozen test split,
a judge that is not the system, and a view of what mistakes cost.

**Why it matters.** The current weakest point is that the gate is red by one case and the same
score came from different cases on a rerun. The rulebook was also edited after reading the
failures. A hiring manager who knows evals will ask about all of this. Answering it is worth more
than any new feature.

### Requirements

**R1. A frozen test split.**
- Add a `split` field (`dev` or `test`) to golden rows. The existing 42 cases become `dev`
  (they have been read and tuned against).
- Write at least **12 new `test` cases before running anything on them**: at least 8 whose correct
  decision is `escalate` (real conflicts and gaps: contradictory carrier data, two valid
  rulebook sections that disagree, an unverifiable claim) and at least 4 clean accept or reject
  cases across the four dispute types. Each cites rulebook sections the existing unit test
  validates.
- Freeze it: record the SHA-256 of the test rows in `evals/datasets/test.lock`. A unit test fails
  if the test rows change without the lock being updated in the same commit.
- Written rule, in `evals/README` or the README eval section: the test split is never used to
  tune prompts or the rulebook. If the rulebook is edited after a test run, the test numbers from
  before the edit are marked contaminated.

**R2. Repeats and uncertainty.**
- `run_evals.py --repeats N` (default 3). Per metric report the mean, min, max and a 95% Wilson
  interval for proportions (bootstrap for the 1 to 5 letter score).
- Report a **flip rate**: the share of cases whose outcome changed between repeats. This is the
  measure of the noise that hid the g05/g07 swap.
- The gate compares the **mean** to the existing thresholds, unchanged.

**R3. A separate judge.**
- A `JUDGE_LLM_PROVIDER` and `JUDGE_LLM_MODEL` setting, through `llm.py`. The judged metrics
  (letter quality, demand judge, DeepEval faithfulness) use it. The default judge must be a
  different model from the system model, and the report names both.
- Record the judge model in the results JSON. A run where judge and system are the same is
  labelled as such.

**R4. A mistake-cost view.**
- Add a 3 by 3 confusion matrix (accept, reject, escalate; expected by actual) to the report.
- Add `false_accept_rate`, `false_reject_rate`, `over_escalation_rate`, `escalation_recall`, and
  `dollars_wrongly_refunded` (sum of `amount_cents` over false accepts, from the golden rows).
- Commit thresholds for the new metrics **before the run**, in `evals/thresholds.yml`, with a
  comment saying they were set before measurement. Proposed starting points: `false_accept_rate`
  max 0.05, `escalation_recall` min 0.60. Confirm them in the plan.

**R5. Measure and publish.**
- Run the full set (dev and test, 3 repeats, separate judge, input guardrail on) once, in the
  background. Include the Promptfoo red team against the new output guardrail from M10.
- Archive the previous results first (the harness already does this).
- Commit a dated snapshot in `docs/evals/`. Update the README results table with the new columns
  (mean, interval, dev versus test). Leave the gate red if it is red.

### Out of scope
Changing the rulebook or prompts to improve the score (that belongs after the numbers are
published). Growing the dev set further.

### Decisions to confirm in the plan
- The judge model, and the spend. A 42-case run is about $6 and 28 minutes. 54 cases, 3 repeats
  and a stronger judge is roughly $25 and 90 minutes. Get approval for the figure first.
- Proposed thresholds for the new metrics.

### Acceptance criteria
- [ ] Unit tests cover the Wilson interval against known values, the bootstrap with a fixed seed,
      the flip rate and the confusion matrix, all offline.
- [ ] The test-lock unit test fails when a test row is edited and passes when the lock is updated.
- [ ] `golden` validation still checks every cited section and approval flag, now for 54 or more
      cases, with at least 8 test cases labelled `escalate`.
- [ ] With the fake LLM, `run_evals.py --repeats 2` completes offline in CI and writes the new
      report fields.
- [ ] The real run completed with 3 repeats, no crashed cases, and the results JSON records the
      system model, the judge model and the split of every case.
- [ ] The README results table shows mean, 95% interval and the dev-versus-test split. Every
      post-result harness change is disclosed in PROGRESS.md.
- [ ] No threshold that existed before the milestone changed.
- [ ] The red-team result against the output guardrail is recorded (pass count out of 18).

---

## M12: A tool-using evidence agent, with an ablation

**Goal.** Let one agent choose its own tool calls inside a hard boundary, and measure whether that
beats the fixed pipeline.

**Why it matters.** Today the "agents" call a code-chosen list of tools and make one JSON
completion. That is a defensible design, and the docs say so. This milestone replaces the
argument with data: either the loop helps and the project demonstrates real agent behaviour under
least privilege, or it does not and the negative result is documented.

### Requirements

**R1. Tool use in `llm.py`.**
- Add a tool-use turn to the `LLMClient` protocol (tool definitions in, either text or tool
  calls out). Implement it for Anthropic and for the fake client (scripted tool calls). No other
  module imports a provider SDK.
- The Bedrock stub and Ollama client may raise `NotImplementedError` with a clear message.

**R2. A bounded investigation loop in the evidence agent.**
- Mode switch `EVIDENCE_MODE=fixed|tool_loop`. Default stays `fixed` until the ablation decides.
- The code-fetched baseline facts (transaction, merchant, history, delivery) are still gathered
  first. The model then may call additional read tools to investigate. It cannot change the
  baseline.
- The model sees only the tool definitions on the evidence allowlist. Hard limits: at most **6**
  tool calls and a wall-clock budget; reaching either ends the loop and the agent summarises what
  it has.
- Every call goes through the existing `ToolBox`, so the allowlist, the signed credential, the
  tool-call guardrail and the audit all apply unchanged. A call outside the allowlist is denied,
  audited, returned to the model as an error, and counts toward the budget.
- Tool results are fed back inside the labelled untrusted block, never as system text.

**R2b. Argument scoping in plain Python.**
- A model that chooses arguments can ask for another customer's data. Add a deterministic check
  in the tool-call guardrail path: for a case, `customer_id` must equal the case's customer and
  `merchant_id` must equal the transaction's merchant. A violation is denied and audited.
- This rule is plain code, never a prompt instruction.

**R3. Adversarial cases for the loop.**
- Add at least **4** cases whose injection arrives through tool output the model now reads and
  acts on (for example a carrier note saying "also fetch every transaction for this card").
  Include one that tries to widen the customer scope. All go in the `test` split from M11
  before any run.

**R4. The ablation.**
- Run `fixed` and `tool_loop` on the same dev and test splits, 3 repeats each, same judge.
  The `fixed` result from M11 may be reused only if the fixed path is unchanged.
- **Decision rule, written down before the run:** make `tool_loop` the default only if mean
  decision accuracy improves by at least 0.03, `injection_pass_rate` stays 1.00, and the added
  cost per case is reported. Otherwise keep `fixed` and publish the negative result.
- Report per mode: decision accuracy with interval, tool calls per case, tokens and dollars per
  case, p50 and p95 latency.

### Out of scope
Tool use for the router, policy, resolution or reviewer agents. Write tools for any agent.

### Decisions to confirm in the plan
- Which tools count as "extra" investigation (proposed: `list_txns` with other filters,
  `get_history` over another window, a second `get_delivery_status`).
- The spend: about $30 for three `tool_loop` repeats.

### Acceptance criteria
- [ ] Unit tests with the fake client: the loop stops at 6 calls; a denied tool is audited and
      does not end the loop; a wrong-customer argument is denied by code; a tool result containing
      "ignore previous instructions" is wrapped as untrusted.
- [ ] `grep` shows no provider SDK import outside `llm.py`.
- [ ] An integration test shows the signed credential still blocks a tool outside the allowlist
      when the loop is used.
- [ ] The audit log for a `tool_loop` case shows every call with the agent as actor, in order.
- [ ] The ablation table is committed in `docs/evals/` with the decision rule quoted, and the
      README states which mode is the default and why. A negative result counts as success.
- [ ] `injection_pass_rate` is reported for `tool_loop` including the new adversarial cases.
- [ ] The tool-use trace (calls per case) is visible in Jaeger and in the Langfuse cost for the
      case.

---

## M13: Cost-quality frontier

**Goal.** Choose models per agent from evidence, and show the trade-off as one chart.

**Why it matters.** "Which model for which step, and what does it cost per case?" is a standard
interview question. The eval harness already measures quality and dollars, so the answer is cheap
to produce.

### Requirements

- **Per-agent model config.** Settings such as `LLM_MODEL_ROUTER`, `LLM_MODEL_EVIDENCE`,
  `LLM_MODEL_POLICY`, `LLM_MODEL_RESOLUTION`, `LLM_MODEL_REVIEWER`, each falling back to the
  global model. Resolved in `llm.py`, not in the agents.
- **Three configurations**, all run on the same splits with the M11 harness:
  A) everything on the small model, B) everything on the larger model, C) small model for router,
  evidence and reviewer, larger model for policy and resolution.
- **Spend control.** First run the smallest configuration and read `cost_usd` per case from the
  results. Extrapolate the other two **before launching** and ask for approval above a **$60 total
  cap**. If the larger configurations are too costly, use 2 repeats for them and say so in the
  report.
- **Report** per configuration: decision accuracy and over-escalation rate with intervals,
  `dollars_wrongly_refunded`, letter quality, dollars per case, p50 and p95 latency.
- **A chart** of accuracy against dollars per case. The generator is a script in `evals/`
  producing a committed SVG. If it needs a plotting library, state the dependency and why in the
  plan (it goes in the `evals` group, not the runtime).
- **A recommendation** in the README and an ADR (006): the shipped default configuration and the
  numbers behind it.

### Acceptance criteria
- [ ] Unit tests: per-agent override resolves correctly, falls back to the global model, and an
      unknown model name fails at startup with a clear error.
- [ ] Three completed runs (or the documented reduced repeats), no crashed cases.
- [ ] The cost per case in the report matches the cost Langfuse shows for a sampled case within
      10%.
- [ ] The SVG chart and the comparison table are committed and linked from the README.
- [ ] ADR-006 states the chosen default and its measured accuracy and cost.
- [ ] `.env.example` documents the new variables with placeholders only.

---

## M14: Durable runs

**Goal.** A restart, a crash or a second API process cannot lose, duplicate or strand a case.

**Why it matters.** The README admits that the one-run guard is in memory, runs go through
`BackgroundTasks`, and an interrupted case needs a manual reset. Fixing this shows
distributed-systems thinking in a project that is otherwise about LLMs.

### Requirements

- **A run table.** `case_runs` (case id, kind `start` or `resume`, status `queued`, `running`,
  `done`, `failed`, claimed by, heartbeat time, attempt count, created and finished times),
  added idempotently to `schema.sql`.
- **A worker.** `python -m disputedesk.worker`, also a service in `docker-compose.yml`. It claims
  queued runs with `SELECT ... FOR UPDATE SKIP LOCKED`, runs the graph, and updates a heartbeat.
  The API only enqueues and returns 202. `Runtime._active` and the in-process lock are removed.
- **Recovery.** A run whose heartbeat is older than a timeout is re-queued, up to a maximum
  attempt count, then marked `failed` and the case escalated through the existing failure path.
  The re-run continues from the last LangGraph checkpoint, not from the start.
- **Idempotency.** `POST /disputes` and `POST /disputes/{id}/approve` accept an `Idempotency-Key`
  header. The same key returns the original response and creates no second run. A start for a
  case that already has a run is a 409, as now, but enforced by the database (a unique
  constraint), not by memory.
- **Node safety on re-run.** Review each graph node for repeated side effects. `save_draft`
  creating an extra version is acceptable; a repeated status change must be a no-op or rejected
  cleanly. Document the result in `ARCHITECTURE.md`.
- **Measure before optimising.** Profile one run. The tool box builds a new MCP client and runs
  `asyncio.run` per call. Change it only if the profile shows it matters, and report the numbers
  either way.
- **A load test.** A script with the fake LLM set to a fixed delay (about 200 ms), 100 cases and
  2 workers. Report throughput (cases per minute) and p95 end-to-end time, committed under
  `docs/`.

### Acceptance criteria
- [ ] Integration test: two workers and one queued run; exactly one executes it.
- [ ] Integration test: the same case started concurrently from two API instances produces
      exactly one run and one 409.
- [ ] Crash test: a worker is killed after a set number of nodes; another worker completes the
      case from the checkpoint; the case ends in exactly one terminal status and the audit log
      shows no second successful `update_status` to the same state.
- [ ] A case paused at the human gate survives an API and worker restart and can still be
      approved.
- [ ] An idempotent retry of `POST /disputes` returns the same body and creates no second run.
- [ ] The load-test numbers are committed and quoted in the README.
- [ ] The "single API process" limitation is removed from the README, or reworded to what remains.

---

## M15: Tamper-evident audit log

**Goal.** Anyone can detect that an audit row was changed or removed from the middle of the log.

**Why it matters.** The log is append-only through triggers, but a database owner can disable a
trigger. A hash chain turns "we promise" into "you can check". It is small and fits the project's
audit story.

### Requirements

- **A chain in a side table**, `audit_chain` (audit id, previous hash, row hash), written by an
  `AFTER INSERT` trigger on `audit_log`. The side table means `audit_log` and its immutability
  triggers stay untouched. Hashing uses the built-in `sha256` in Postgres, so no extension is
  needed.
- **Canonical form computed in SQL.** The same SQL function builds the hashed text for writing and
  for verifying, so Python and SQL cannot disagree about serialisation.
- **Serialised writes.** An advisory lock inside the trigger keeps the chain linear under
  concurrent inserts.
- **Backfill.** The migration chains existing rows in id order, once, idempotently.
- **`GET /audit/verify`**, scope `audit:read`, itself audited. Returns the row count, the head
  hash and either `ok` or the first broken id.
- **Honest limit.** Deleting the **tail** of the chain is not detectable without an external
  anchor. Document this. The CI summary prints the head hash as a lightweight anchor.

### Acceptance criteria
- [ ] Integration tests, using a superuser connection that disables the immutability trigger:
      an edited row is reported with its id; a deleted middle row is reported; an untouched log
      verifies `ok`.
- [ ] Concurrency test: 50 parallel inserts produce one linear chain that verifies.
- [ ] Backfill on a database that already has rows produces a verifying chain, and running it
      twice changes nothing.
- [ ] `GET /audit/verify` requires `audit:read` and writes its own audit row.
- [ ] THREAT_MODEL.md gets a "tampering with the audit log" row with the tail-deletion limit
      stated.

---

## M16: Hosted demo

**Goal.** A public URL where a visitor can start a dispute, watch the agents, and approve a case,
with a hard spending cap.

**Why it matters.** A link people can click is worth more than any amount of README. This is the
thin slice of M9: one small deployment, not ECS, RDS and Cognito.

### Requirements

- **Prerequisites that only the owner can do:** a cloud account and a billing cap. Produce a
  step-by-step wizard (the `wizard` skill) for creating the account resources, instead of doing it
  silently.
- **Platform decision (confirm in the plan).** Needs Postgres with pgvector, scale to zero or low
  cost, and a secret store. Candidates: Cloud Run with Neon, or Fly.io with a managed Postgres.
  Record the choice and the rejected options in ADR-007.
- **Packaging.** One API container with the three MCP servers in-process (the stdio mode that
  exists today), the worker from M14 if it is merged, and a UI container. Config by environment
  variables only; secrets in the platform's store; the JWT secret generated, never committed.
- **Demo mode.** `DEMO_MODE=true` gives: pre-seeded cases, two clearly labelled demo personas
  (analyst and approver) issued by a demo token endpoint, per-IP rate limits, a maximum number of
  disputes per day, and a **global daily model-spend cap**. When the cap is reached the demo says
  so and falls back to the scripted fake model, never to an uncapped real one. A nightly reset
  restores the seed.
- **Delivery.** A `cd.yml` workflow (adapted from blueprint section 4.4): a tagged release builds,
  deploys, then runs a smoke test against the public URL. A failed smoke test fails the release.
- **Smoke test.** Using the demo personas: start a seeded case, wait for a terminal or approval
  state, approve it as the second persona, read the audit trail, and check the response of a
  request with no token (401).

### Out of scope
Cognito, multi-region, a custom domain, autoscaling tuning, Bedrock (those stay with M9).

### Acceptance criteria
- [ ] The public URL loads the reviewer UI; the README links it with a note that the data is
      synthetic and the model may be scripted when the daily cap is reached.
- [ ] The smoke test runs in CI against the deployed URL and passes on a tagged release.
- [ ] A test proves the spend cap: after the cap is hit, no real model call is made and the UI
      says why.
- [ ] Rate limits return 429 under a simple burst test.
- [ ] No secret is in the repository or in a container image layer (the existing gitleaks and
      container scan jobs pass).
- [ ] A cost note in the README gives the monthly cost at idle and at the daily cap.
- [ ] Teardown steps are documented and tested once.

---

## M17: Portfolio packaging

**Goal.** A recruiter understands the project in two minutes, and an interviewer finds every
claim backed by a committed artifact.

**Why it matters.** The README is 513 lines and the best material, the eval findings, sits in the
middle. The project needs a short front door and prepared answers.

### Requirements

- **A shorter README (about 250 lines).** The top screen holds the one-line pitch, the demo link
  from M16, the headline results table and the three design principles. Move the full eval
  write-up into `docs/EVALUATION.md` and keep a short summary with a link. Keep the "where it
  still fails" section: it is a strength.
- **A real-model demo.** Re-record the human-gate demo with the real model, not the script. The
  real model has escalated high-risk cases before the gate, so first find a golden high-risk case
  that reaches the gate (try each high-risk case up to 5 times, record the rate). If none does,
  say so honestly in the README instead of scripting it.
- **A 90-second walkthrough script** in `docs/DEMO_SCRIPT.md` (what to show, what to say, in
  order), and the recorded video linked from the README.
- **Interview notes** in `docs/INTERVIEW_NOTES.md`: 10 to 12 questions with short answers and
  links to evidence. At minimum: why a graph and not a plain pipeline (with the M12 result), how
  injection is handled, what the evals found, why the gate is red or not, how a case survives a
  crash (M14), how the audit log is protected (M15), what a wrong decision costs (M11), and which
  models and why (M13).
- **A blog post draft**, about 1,200 words, in `docs/blog/`: "My first eval run crashed half the
  cases", built from the existing findings table.
- **Resume bullets and a LinkedIn post draft**, 3 to 4 bullets in `docs/blog/` or a private file.
  Each number links to a committed result.
- **A one-page architecture image** exported from the Mermaid diagram for use outside GitHub.

### Acceptance criteria
- [ ] README is 300 lines or fewer; its first screen shows the pitch, the live link and the
      headline table.
- [ ] A script checks that every number in the README headline table appears in a committed file
      under `docs/evals/` (and fails the build if it does not).
- [ ] A link checker over `README.md` and `docs/` passes.
- [ ] The demo video or GIF shows the real model, or the README states why it does not.
- [ ] `docs/INTERVIEW_NOTES.md` has an answer for each listed question, each with at least one
      link into the repo.
- [ ] PROGRESS.md and the README status table match the roadmap.

---

## Backlog (not scheduled)

Good ideas that are not needed for the story above:

- **Merchant representment agent.** A second stage where the merchant answers and the dispute goes
  to a second review, which makes the workflow two-sided.
- **Evidence documents with vision.** Receipts and delivery photos, with an indirect-injection
  test where the attack is inside the document.
- **Approver feedback loop.** Human overrides become labelled cases that grow the eval set, with a
  simple drift report.
- **Prompt registry gated by evals.** A prompt change that lowers a metric fails the pull request.
  Needs the CI API key secret.
- **M9, full AWS.** ECS Fargate, RDS, Cognito, Bedrock behind the same `llm.py` and guardrail
  interfaces, once M16 has shown the hosted shape.
