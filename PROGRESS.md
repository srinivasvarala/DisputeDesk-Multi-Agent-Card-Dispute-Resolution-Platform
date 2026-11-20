# DisputeDesk progress

Milestone status against [PROJECT_BLUEPRINT.md](PROJECT_BLUEPRINT.md). Updated at the end of each milestone.

| Milestone | Status | Completed | Commits |
|---|---|---|---|
| M0: Scaffold | ✅ Done | 2026-11-19 | `e473ea0`, `c1098da` |
| M1: Data and MCP servers | ✅ Done | 2026-11-19 | `5e295cf`, `27ecb5b`, `3b7fdb4`, `7850a87`, `d4d52e9` |
| M2: RAG over dispute policies | ✅ Done | 2026-11-19 | `27fd98f`, `8a32400`, `0631988`, `1748131`, `ae83fc6`, `7e8f6a3`, `9c5d32c`, `7537998` |
| M3: Multi-agent graph | ✅ Done | 2026-11-19 | `db8dfe8`, `3666e3e`, `0df42de`, `183817d`, `d095789`, `278f927`, `a7a348a`, `57183d1`, `6827e19`, `4c5c935`, `887b0f2` |
| M4: Guardrails | ✅ Done | 2026-11-19 | (see git log, `feat(guardrails)`) |
| M5: Security | ⏳ Not started | | |
| M6: Observability | ⏳ Not started | | |
| M7: Evals | ⏳ Not started | | |
| M8: UI, docs and polish | ⏳ Not started | | |
| M9: AWS deployment (phase 2) | ⏳ Not started | | |

Legend: ✅ Done · 🚧 In progress · ⏳ Not started · ⚠️ Blocked

---

## M0: Scaffold ✅

**Goal:** a repo that builds, lints, tests and runs infra locally.

### Delivered
- uv project (`pyproject.toml`), Python 3.12; ruff, strict mypy, pytest; pre-commit hooks.
- Repo layout from blueprint section 2 (empty packages for later milestones).
- `config.py`: pydantic-settings, env-only config, secrets as `SecretStr`, default `LLM_PROVIDER=fake`.
- `llm.py`: one `LLMClient` interface + `get_llm()` factory.
  - Providers: `anthropic` (default model `claude-opus-5-5`, server-side refusal fallback), `ollama`, `fake`, `bedrock` (stub).
  - Uniform errors: `LLMError`, `LLMRefusalError`, `LLMTruncatedError`.
- `docker-compose.yml`: Postgres 16 + pgvector, Langfuse v2 + its own DB, OTel collector.
- `.env.example` (placeholders only), `.gitignore`.
- 12 offline unit tests (mocked HTTP for Anthropic and Ollama; env isolated per test).
- `.github/workflows/ci.yml`: lint, format check, mypy, unit tests.
- README skeleton, `docs/adr/001-stack-choices.md`.

### Acceptance
| Criterion | Result |
|---|---|
| `docker compose up` is healthy | ✅ All 4 services healthy; pgvector 0.8.7 enabled; OTLP accepts traces |
| `uv run pytest` passes | ✅ 12 passed |
| ruff and mypy clean | ✅ |
| CI green on first push | ✅ [Run 44620018996](https://github.com/srinivasvarala/DisputeDesk-Multi-Agent-Card-Dispute-Resolution-Platform/actions/runs/44620018996) passed on `2634756` |

### Decisions
- Langfuse v2 instead of v3 (v3 needs ClickHouse, Redis, MinIO). See ADR-001.
- `httpx2` instead of `httpx`: same HTTP stack as the Anthropic SDK 2.x.
- CI steps for RAG ingest and integration tests are commented out until M2/M3.

### Known gaps / carried forward
- OTel collector healthcheck only validates config (distroless image); live check is `http://localhost:13133/`.
- `OllamaClient` does not close its HTTP client. Address in M3 when client lifecycle is decided.
- Langfuse v2 has no OTLP ingest; LLM traces go via the Langfuse SDK in M6.

---

## M1: Data and MCP servers ✅

**Goal:** agents have tools and data to call.

### Delivered
- `db/schema.sql` (customers, merchants, transactions, shipments, cases, case_drafts, audit_log), applied idempotently by code.
- `db/seed.py`: deterministic synthetic data (seed 42, fixed reference time 2026-11-01): 50 customers, 20 merchants, 200 transactions, 96 shipments, 40 disputes (12 fraud, 10 not received, 8 duplicate, 10 not as described). High-risk merchants attract more disputes; some "not received" claims are contradicted by a delivered shipment, so not every case is an easy accept. Duplicate charges have no shipment of their own; "not as described" disputes are only on delivered orders. `--reset` reloads app tables and never touches `audit_log`.
- Three MCP servers on the official MCP SDK (`MCPServer`, formerly FastMCP), each runnable standalone with `python -m disputedesk.mcp_servers.<name>` (stdio default, `--transport streamable-http`, ports 8101-8103):
  - `transactions` (read-only): `get_txn`, `list_txns`, `get_history`
  - `merchant` (read-only): `get_profile`, `get_dispute_rate`, `get_delivery_status`
  - `cases`: `get_case` (read); `save_draft` (append-only write), `update_status` (state change)
- `AuditedMCPServer`: exactly one `audit_log` row per tool call (actor, server, tool, case_id, arguments, outcome, error, duration), including rejected arguments, unknown tools, crashes and cancellations.
- `scripts/mcp_client_demo.py`: lists and calls every tool over stdio, makes one invalid call per server, prints the audit rows written by the run.
- CI now seeds Postgres and runs `tests/integration`.

### Acceptance
| Criterion | Result |
|---|---|
| Each server starts standalone | ✅ stdio (integration test per server) and streamable-http (manual check) |
| A client script lists and calls every tool | ✅ `scripts/mcp_client_demo.py`, exit 0; also run by an integration test |
| Unit tests cover each tool, including invalid input | ✅ 110 unit tests: every tool's happy path plus bad ID format, missing/extra arguments, out-of-range values, not found and illegal state changes, each asserting its audit row |
| Every call writes to audit_log | ✅ asserted in unit tests (in-memory) and integration tests (Postgres) |
| Integration | ✅ 20 passed (PostgresStore matches InMemoryStore; seed reload is idempotent; writes roll back if their audit row fails; concurrent writes on the shared connection stay consistent) |
| CI green | ✅ [Run 44631672292](https://github.com/srinivasvarala/DisputeDesk-Multi-Agent-Card-Dispute-Resolution-Platform/actions/runs/44631672292) passed on `25fc537`, including seed + integration steps |

### Decisions
- **mcp 2.x**: FastMCP is now `MCPServer` in the official SDK; we use it rather than the separate `fastmcp` package.
- **Flat, typed tool arguments** (shared `Annotated` types with regex IDs and bounds) instead of one wrapper model per tool, so agents call `{"txn_id": ...}` rather than `{"params": {...}}`. The SDK still validates each call against a generated Pydantic model. Unknown arguments, which the SDK silently drops, are rejected by `AuditedMCPServer`.
- **Read/write separation** at three levels: MCP tool annotations (`readOnlyHint` / `destructiveHint`), separate `ReadStore` / `CaseWriteStore` / `AuditSink` protocols (the transactions and merchant servers never receive a write store), and separate read/write registration in the cases server. Allowlist names for M5: `cases.get_case`, `cases.save_draft`, `cases.update_status` (the blueprint's `cases.draft_*` maps to `save_draft`).
- **Case workflow** in plain Python (`cases/workflow.py`): explicit transition table; status updates are compare-and-set. Resolving must match the latest draft (accept → `resolved_accepted`, reject → `resolved_rejected`); `pending_approval` needs an accept or reject draft. A human override of the draft will be a separate, explicit path (M3/M5).
- **Audit is fail-closed and atomic for writes**: write tools store their ok row in the same transaction as the change (`audited_write`), so a change never commits without its audit row. If any audit row cannot be written, the call is refused with a generic message. Crash and DB details go to the audit row or server log, never to the client.
- **One shared Postgres connection per server process**, guarded by a lock held for each statement and whole transaction. A connection pool (`psycopg_pool`) is the upgrade if concurrency ever matters.
- **`get_delivery_status`**: `applicable` reflects whether the merchant ships goods; `shipment_found=false` with `applicable=true` means the order was never shipped.
- **Unit tests use `InMemoryStore`**; Postgres is covered by integration tests that assert both stores return identical results.
- No Faker: hand-written fictional name/merchant lists with `random.Random(seed)`.

### Known gaps / carried forward
- Actor is the MCP client's self-reported name. Verified identity and per-agent tool scopes: M5.
- `audit_log` is append-only by convention (insert-only API); a DB-level guard (trigger/permissions) lands in M5.
- `audit_log.arguments` stores raw arguments (synthetic data only). Presidio redaction: M4.
- `customer_narrative` is untrusted text and is only labelled as such in the tool output schema. Wrapping it as data happens in M5.
- `get_history` / `get_dispute_rate` windows end at the current time, while seed data is anchored to 2026-11-01, so short windows will empty out as real time moves on. Fine for a demo; reseeding with a new anchor would fix it.
- Integration tests reseed the local dev database (app tables only).
- Code review (`/code-review`) findings: all 9 fixed in `d4d52e9`.


---

## M2: RAG over dispute policies ✅

**Goal:** cited policy answers.

### Delivered
- `data/policies/`: 15 fictional rulebook docs (reason codes F10/F11, R20/R21, P30, Q40-Q42 mapped to the seed's `reason_code`; time limits; evidence standards; refunds; approval thresholds; representment; merchant risk; decision letters). Every doc carries a "fictional policy, not real network rules" banner. Numbers are consistent across docs and `08-time-limits.md` is authoritative for deadlines.
- `llm.py`: `Embedder` and `Reranker` protocols with fastembed implementations (`BAAI/bge-base-en-v1.5`, `BAAI/bge-reranker-v2-m3-int8`, local ONNX, no API key) and deterministic fakes for tests. Still the only module importing a model SDK.
- `rag/ingest.py`: heading-aware chunking (sections never cross a heading; `section` is the heading path, e.g. `Delivery proof > Signature confirmation above $250`; long sections split on paragraphs; banner dropped; code fences respected) -> 92 chunks. One table with `vector(768)` + HNSW and a generated, weighted `tsvector` (headings A, body B) + GIN.
- Idempotent ingest: keyed by `(source, chunk_index)` with a content hash and model name; only new or changed chunks are embedded, removed chunks deleted, all in one transaction (including `--rebuild`). Refuses an empty corpus and a vector-size change without `--rebuild`. CLI: `python -m disputedesk.rag.ingest`.
- `rag/retriever.py`: vector top 20 + keyword top 20 (stemmed terms OR-ed into a tsquery) -> reciprocal rank fusion (k=60) -> cross-encoder rerank of the top 10 fused candidates. Results carry `source`, `section`, `citation` and per-list ranks. Fails loudly on a missing, empty or wrong-model index. CLI: `python -m disputedesk.rag.retriever "question"`.
- `rag/evaluation.py` + `evals/datasets/retrieval.jsonl`: 25 labelled questions (10 direct, 15 harder: near-miss sections, cross-document, numeric edge cases, paraphrased vocabulary). Reports hit@k and MRR per configuration; `--reranker-model` and `--rerank-top-n` compare models and pool sizes.
- `agents/policy.py`: `PolicyAgent.answer() -> PolicyAnswer{summary, citations[]}`. The retriever is its only tool. Citations reference shown excerpts by id (source/section filled from the chunk, never the model); quotes need at least 2 words, must appear in the excerpt (ignoring case, whitespace, markdown emphasis), and the stored quote is the excerpt's own text; no valid citation -> one retry -> `PolicyAgentError`. The question is untrusted: escaped, in the user message, never in the system prompt. Two `audit_log` rows per answer (`policy_agent` / `rag`): `search_policies` and `answer_policy_question` (the decision, or why there was none), including failures. Audit is fail-closed: if a row cannot be written the call is refused.
- CI runs ingest and caches model weights; the retrieval-quality test writes its table to the job summary.

### Acceptance
| Criterion | Result |
|---|---|
| 10-question retrieval set reaches hit@5 >= 0.8 | ✅ Grown to 25 harder questions; hit@5 **0.92** (hybrid + rerank), gated in `tests/integration/test_retrieval_quality.py` |
| Every answer carries at least one citation | ✅ Enforced in code (`PolicyAnswer.citations` min 1, verbatim-quote check); integration test over all 25 questions |
| Ingestion is idempotent | ✅ Second run: 0 embedded, 92 unchanged (1.3s); tests cover change/add/remove, model change, dimension change, rollback |
| Lint, types, tests | ✅ ruff, mypy; 172 unit, 47 integration |
| CI green | ✅ [Run 44652878826](https://github.com/srinivasvarala/DisputeDesk-Multi-Agent-Card-Dispute-Resolution-Platform/actions/runs/44652878826) passed on `9cd6ac2` in ~4 min, including ingest, model download + cache, and the retrieval-quality gate; [run 44654741164](https://github.com/srinivasvarala/DisputeDesk-Multi-Agent-Card-Dispute-Resolution-Platform/actions/runs/44654741164) passed again on `7537998` after the code-review fixes |

Retrieval results on the 25-question set (k=5):

| Configuration | hit@5 | MRR | Latency/query (CPU) |
|---|---|---|---|
| vector | 0.84 | 0.73 | |
| keyword | 0.72 | 0.54 | |
| hybrid (RRF) | 0.88 | 0.72 | 0.04s |
| hybrid + bge-reranker-base (all 40) | 0.92 | 0.63 | 10.4s |
| hybrid + ms-marco-MiniLM-L-12-v2 (all 40) | 0.88 | 0.73 | 2.7s |
| hybrid + jina-reranker-v1-turbo-en (all 40) | 0.72 | 0.51 | |
| hybrid + bge-reranker-v2-m3-int8 (all 40) | 0.92 | 0.75 | 11.8s |
| **hybrid + bge-reranker-v2-m3-int8 (top 10, default)** | **0.92** | **0.75** | **2.5s** |

### Decisions
- **fastembed** for embeddings and reranking: local, free, works in CI offline once cached. Embedder size matters least at this corpus size; hybrid search and reranking matter more.
- **Reranker chosen by measurement**: only `bge-reranker-v2-m3-int8` beat no-rerank on both hit@5 and MRR. Reranking only the top 10 fused candidates kept full-pool quality at ~5x lower latency.
- **OR keyword queries** instead of `websearch_to_tsquery` (AND): natural-language questions almost never matched with AND.
- **Contextual chunk text**: title + section are prepended for embedding and reranking, and the tsvector weights headings higher.
- **No pgvector Python package**: vectors are passed as text literals.
- **Grounding enforced in code, not prompt**: the model can only cite excerpts it was shown, by id, with verified quotes.
- `rag.ingest` has no `--seed` flag (the blueprint's CI line used one); the policies directory is the only input.
- q22's label was widened after the first run to the parent "Representment" section, which states the one-round rule. Recorded here because it changed after seeing results.

### Known gaps / carried forward
- Two questions miss in every configuration: q11 (everyday wording vs "Order of precedence") and q23 (answer lives in a markdown table, which embeds poorly). Candidates: row-level table chunks, query rewriting.
- 25 questions is small: differences of 0.03 MRR are one or two rank changes.
- Reranking is 2.5s/query on a laptop CPU; first run downloads ~0.8 GB of weights.
- `PolicyAnswer` quality with a real LLM is not yet evaluated (M7). Tests use a deterministic stand-in.
- Audit rows store the raw question (synthetic data only); Presidio redaction is M4.
- Code review (`/code-review since 27c0608`, 7 findings): fixed an empty/one-word quote passing as a citation, stored quotes not being the policy's literal text, audit failures masking the search result (now fail-closed) plus no audit row for the decision, the eval labelling plain hybrid as "rerank" when no reranker is configured, the bge query prefix applying to non-bge models, and `Tier C#`-style headings losing the `#`. Not reproducible: backslashes breaking `to_tsquery` (regression test added). `Priority #` -> `Priority` is correct CommonMark and was left alone.

---

## M3: Multi-agent graph ✅

**Goal:** one dispute goes end-to-end through the agents.

### Delivered
- `graph/`: LangGraph over a Pydantic `DisputeState`. Flow: `intake -> router -> evidence -> policy -> resolution -> reviewer`, then `finalize` (low risk), or `request_approval -> human_gate -> finalize` (approval required), or `escalate`. The graph is built from injected nodes (`Nodes`), so the skeleton was built and tested on stubs first (`graph/stubs.py`) and real nodes replaced them one at a time. Conditional edges are pure functions in `graph/routing.py`.
- **Reviewer loop:** the reviewer may send a draft back at most 2 times; the third "revise" escalates. A draft that itself says `escalate` skips review. Every revision is a new draft version (all kept).
- **Human gate:** `interrupt()` pauses at a Postgres checkpoint; `Command(resume=...)` continues. Approval is its own node (`request_approval`, sets `pending_approval`) separate from the gate, because LangGraph re-runs an interrupted node from the top on resume. A human "no" escalates.
- **Agents** (`agents/`), each with its own allowlist in one file (`allowlists.py`):
  - `router_agent`: get_txn, get_profile. The LLM picks the reason code; **risk tier and approval requirement are plain Python** (`graph/rules.py`): HIGH if the merchant is high-risk or amount >= `HUMAN_APPROVAL_THRESHOLD_CENTS` ($500), MEDIUM for a medium-risk merchant or any fraud claim.
  - `evidence_agent`: read-only tools (get_txn, list_txns, get_history, get_profile, get_dispute_rate, get_delivery_status). Which tools run is decided by code from the reason code; facts in the result are tool output, the model only writes the summary and a leaning.
  - `policy_agent`: the existing M2 agent, wrapped. Its question is built only from structured fields (no customer text).
  - `resolution_agent`: save_draft only. Cannot change status.
  - `reviewer_agent`: no tools.
  - `dispute_workflow` (no LLM): get_case, update_status; owns every status change.
- **ToolBox** (`agents/toolbox.py`): tools run in-process through a real MCP client named after the agent, so the servers' audit rows carry the right actor. A call outside the allowlist is refused before reaching a server and is audited.
- **Resilience** (`agents/resilience.py`): `call_with_retry` (timeout per attempt, exponential backoff, non-retryable errors) used by `ResilientLLM` (wraps any `LLMClient`), `ResilientSearch` (RAG) and `ToolBox`. Timeouts/retries come from settings.
- **Structured outputs:** every agent returns a Pydantic model; `complete_json` validates, retries once with the error, then fails. Customer/merchant text is only ever escaped data in the user message.
- **Audit:** every tool call (by the MCP servers) and every agent decision (`classify_dispute`, `assess_evidence`, `decide_dispute`, `review_draft`, `answer_policy_question`, `workflow_failed`) writes to `audit_log`, success or failure.
- **API** (`api/`): `POST /disputes` (start an existing open case, 202, runs in background), `GET /disputes/{id}` (case status, workflow state, route, draft, review, revision count, path), `POST /disputes/{id}/approve` (`{approved, approver, note}`). 404 unknown case, 409 wrong state (already started, not waiting, already being processed), 422 bad body. A crashed run escalates the case and audits it.
- **Fake LLM** (`agents/fake_llm.py`): deterministic, agent-aware; `LLM_PROVIDER=fake` runs the whole system offline. `scripts/run_disputes.py` drives N seeded disputes through a running API and approves the ones that pause.
- Postgres checkpointer with an explicit type allowlist (`graph/serde.py`, derived by walking the state's types).
- `seed --reset` now also clears workflow checkpoints (they are keyed by case id).
- Tests: 239 unit, 53 integration (7 new in `tests/integration/test_dispute_graph.py`).

### Acceptance
| Criterion | Result |
|---|---|
| 5 seeded disputes complete end-to-end | ✅ Live run against uvicorn + Postgres + real retriever: 7 disputes (`scripts/run_disputes.py --count 7`), all `completed`; also a test (`test_five_seeded_disputes_complete_end_to_end`) |
| A high-value dispute pauses for approval and resumes after `approve` | ✅ `pending_approval` at the pause, `resolved_*` after; test resumes on a brand-new runtime (as after a restart) and asserts no agent is called again |
| Integration tests: happy path and reviewer-rejection path | ✅ Plus one-revision-then-approve, human decline, audit trail per agent |
| Max 2 reviewer revisions | ✅ Third request escalates with exactly 3 drafts (unit + integration) |
| Lint, mypy (strict), tests | ✅ ruff clean, mypy clean (55 files), 239 + 53 passing |
| CI green | ✅ [Run 44658984015](https://github.com/srinivasvarala/DisputeDesk-Multi-Agent-Card-Dispute-Resolution-Platform/actions/runs/44658984015) passed on `887b0f2` (lint, mypy, unit, seed, ingest, integration) |

### Decisions
- **`POST /disputes` starts the workflow for an existing open case** (`{"case_id": "case_0001"}`). The blueprint did not say whether it creates cases; there is no create-case tool in the `cases` server, and cases come from the seed. Creating cases is a small addition if wanted.
- **Tools in-process, not over stdio MCP:** same server objects and audit, no subprocesses, easy tests. The `ToolBox` interface lets a networked client replace it later.
- **Sync graph, run on worker threads:** FastAPI runs the graph in a background task; no async rewrite of the agents.
- **Approval rule** (my default, confirmed by "go"): HIGH risk (amount >= $500 or high-risk merchant) needs a human for accept and reject alike. The Evidence agent's `list_txns` was missing from its allowlist at first; the allowlist check caught it in the first test.
- **Completion is read from `final_status`**, not from an empty `next`: a live poll showed LangGraph can checkpoint between nodes with no `next` yet, which briefly reported `completed`. Regression test added.
- **`tool_index` runs its async lookup on its own thread**: the first live start crashed inside uvicorn's event loop (`asyncio.run` in a running loop); unit tests had missed it. Regression test added.
- **`final_status` and `path` live in state** so the API and tests can show the route a case took.
- New dependencies: `langgraph` + `langgraph-checkpoint-postgres` (graph runtime, interrupt/resume, Postgres checkpointer), `psycopg-pool` (checkpointer connections), `fastapi` + `uvicorn` (API), `httpx` dev-only (Starlette `TestClient`).

### Known gaps / carried forward
- **No refund cap or required-evidence check in code yet.** Only the approval threshold is enforced (router rules). The refund cap, per-agent allowlist enforcement at the guardrail layer, and PII redaction are M4 (`policy_rules.py`, `guardrails/interface.py`).
- **No auth:** the `approver` name comes from the request body. JWT roles are M5.
- **Single API process:** the "already being processed" guard is in memory. Several workers would need a database lock. After a restart mid-run a case keeps its checkpoint but nothing drives it: `POST /disputes` and `/approve` both return 409, so it needs a manual reset (no recovery endpoint yet).
- **Timeouts abandon, not cancel, a hung LLM or retrieval thread** (Python cannot kill a thread); the result is dropped. Tool calls do cancel (`anyio.fail_after`).
- **A crashed run is escalated, not retried or resumable** through the API.
- **Agent quality with a real model is untested** (fake LLM only); that is M7. Prompts have not been tuned.
- Evidence facts for `get_history`/`get_dispute_rate` carry no `case_id`, so those audit rows are not filterable by case (they are by actor and time).
- `OllamaClient` still does not close its HTTP client (carried from M0).

---

## M4: Guardrails ✅

**Goal:** unsafe input/output is blocked, deterministic rules are enforced in code.

### Delivered
- `guardrails/interface.py`: `check_input`, `check_output`, `check_tool_call` returning a `Verdict` (`allow | redact | block | deny | require_approval`). Fails closed if a NeMo check errors.
- `guardrails/policy_rules.py` (plain Python, no NeMo/LLM): per-agent allowlist (reads `agents/allowlists.py`) and the refund cap (`human_approval_threshold_cents`). Deny wins over the cap.
- `guardrails/nemo_edge.py` + `nemo_config/` (Colang 1.0): input rails (jailbreak/injection, off-topic) and output rails (PII, grounding). Edges only; LangGraph still owns the flow. Rails are Python actions, so no provider SDK is touched.
- `guardrails/judges.py`: regex heuristics plus an optional `LLMJudge` that goes through `llm.py` and fails closed. Grounding = every `$` amount and card last-4 in the output must appear in the supplied case material.
- `guardrails/redaction.py`: Presidio (card, SSN, email, phone, IBAN) and a logging filter. PERSON is excluded on purpose (too many false positives on merchant names).
- `guardrails/telemetry.py`: `guardrail.rail/action/agent/triggered` + `case_id` as a span event, and `guardrail.triggered` on the span. OTel API only, so it is a no-op until M6 adds the SDK.
- `ToolBox.call` now delegates to `check_tool_call` (raises `ToolDeniedError` or the new `ApprovalRequiredError`).
- Denied/redacted/blocked decisions go to `audit_log` (rail and entity types only, never the text). Allowed tool calls are not re-audited because the MCP servers already do it.

### Acceptance
| Criterion | Result |
|---|---|
| PII input is redacted | ✅ `test_pii_in_input_is_redacted` |
| Jailbreak prompt is blocked | ✅ `test_jailbreak_is_blocked` |
| Tool outside allowlist is denied | ✅ `test_forbidden_tool_is_denied`, `test_toolbox_enforces_refund_cap_before_calling_the_server` |
| Refund over cap forces human approval | ✅ `test_refund_over_cap_requires_human_approval` |
| Events emitted as trace attributes | ✅ `test_guardrail_events_are_emitted_as_trace_attributes` |

### Decisions
- Rails are Python actions behind Colang flows, with no NeMo-managed LLM, to keep the "all LLM calls via `llm.py`" rule.
- `Guardrails(config, use_iorails=False)` instead of `LLMRails`, see issues below.
- Only the tool-call check is wired into the system (per instruction). `check_input`/`check_output` are not yet called by the graph.
- `en_core_web_sm` (not `lg`) to keep CI fast.

### NeMo / dependency issues flagged
1. **`LLMRails` is deprecated in 0.25.0** (removal no earlier than 0.27). The replacement `Guardrails` falls back to it for custom Colang flows, because the new IORails engine only supports catalogued rails. We pass `use_iorails=False`. Revisit on upgrade.
2. **Presidio pins `numpy<2.5`**, which downgraded numpy 2.5.5 to 2.4.6. The full unit and integration suites pass.
3. NeMo emits two `DeprecationWarning`s of its own (`nim_url`, `get_event_loop`) in tests. Not ours to fix.
4. `nemoguardrails` has no `py.typed`, so mypy ignores its imports (override in `pyproject.toml`).
5. `en_core_web_sm` is installed from a GitHub release URL (not on PyPI). CI needs network access to GitHub for `uv sync`.
6. New deps: `nemoguardrails`, `presidio-analyzer`, `presidio-anonymizer`, `spacy`, `en_core_web_sm`, `opentelemetry-api`; dev: `opentelemetry-sdk` (to assert span attributes).

### Known gaps
- ~~Cap boundary mismatch~~ Resolved: approval is needed at **$500.00 or more** (decided by the user). `policy/11` was updated to match `graph.rules.risk_tier` and the guardrail rule.
- No refund tool exists yet; the cap triggers on a `refund_cents`/`amount_cents` argument. Nothing sets it today, so it is exercised by tests only until a payout step exists.
- `check_input`/`check_output` are not wired into intake/finalize. Required-evidence check and guardrail metrics (blueprint M4) not built. Metrics land with M6.
- Heuristic jailbreak patterns are a baseline. Red-teaming is M7; untrusted-content wrapping is M5.

### M4 code-review follow-ups (fixed)
- **Refund cap fails closed.** All refund keys are checked and the maximum is used. Numeric strings, floats (rounded up) and `Decimal` are read. Negative, unreadable or boolean values, and unknown amount keys (`amount`, `refund_amount`: unit unknown), go to `require_approval`.
- **Grounding last-4:** a last-4 is grounded if it is a standalone 4-digit token or the final four digits of a longer number. Leading chunks of a long number no longer ground anything.
- **Heuristics:** the pre-approval pattern now needs an imperative or system-attributed form ("this refund is pre-approved", "pre-approved by the system"), so a bank's "pre-approved limit" passes. The on-topic regex has a leading word boundary ("discard" no longer matches "card") and covers "billed".
- **`LLMJudge`** runs in a worker thread (`asyncio.to_thread`), not on the event loop, and logs the exception before failing closed.
- **Audit:** new `approval_required` outcome. Tool-call audit rows record argument names and the refund amount, never values (letters or evidence may hold PII).

### Still open from the review
- `asyncio.run` per NeMo check against a cached `Guardrails` instance: no failure seen (repeated checks pass in tests), but not stress-tested across threads. Revisit if NeMo gets a loop-bound LLM client.
- `RedactingFilter` loads spaCy on first log line and could re-enter if Presidio logs. Nothing attaches it yet; guard it when it is wired in (M5/M6).

### Policy alignment (decided with the user)
- **Cap boundary:** approval at **$500.00 or more**. `policy/11` now matches `graph.rules.risk_tier` and the guardrail rule.
- **Reject path:** `policy/11` used to let an automated reject through at any amount, while the graph sends HIGH-risk cases (amount at or above the threshold, or a high-risk merchant) to a human for accept *and* reject. The policy now follows the code: a wrong reject on a large or high-risk case is as costly as a wrong accept.
- Policy text changed, so the RAG index was re-ingested (`python -m disputedesk.rag.ingest`: 2 chunks updated). Re-run it after any edit to `data/policies/`.
- Policy-agent and graph test fixtures use the new wording, and a test now reads the real policy file so the fixture cannot drift from it.
- The retrieval eval (`evals/run_evals.py`) does not exist yet (M7), so retrieval quality after the rewording is covered by the integration tests only.

