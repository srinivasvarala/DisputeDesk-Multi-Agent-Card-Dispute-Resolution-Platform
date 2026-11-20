# DisputeDesk progress

Milestone status against [PROJECT_BLUEPRINT.md](PROJECT_BLUEPRINT.md). Updated at the end of each milestone.

| Milestone | Status | Completed | Commits |
|---|---|---|---|
| M0: Scaffold | ✅ Done | 2026-11-19 | `e473ea0`, `c1098da` |
| M1: Data and MCP servers | ✅ Done | 2026-11-19 | `5e295cf`, `27ecb5b`, `3b7fdb4`, `7850a87`, `d4d52e9` |
| M2: RAG over dispute policies | ✅ Done | 2026-11-19 | `27fd98f`, `8a32400`, `0631988`, `1748131`, `ae83fc6`, `7e8f6a3`, `9c5d32c` |
| M3: Multi-agent graph | ⏳ Not started | | |
| M4: Guardrails | ⏳ Not started | | |
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
- `agents/policy.py`: `PolicyAgent.answer() -> PolicyAnswer{summary, citations[]}`. The retriever is its only tool. Citations reference shown excerpts by id (source/section filled from the chunk, never the model) and quotes must appear verbatim; no valid citation -> one retry -> `PolicyAgentError`. The question is untrusted: escaped, in the user message, never in the system prompt. Each retrieval writes one `audit_log` row (`policy_agent` / `rag` / `search_policies`), including failures.
- CI runs ingest and caches model weights; the retrieval-quality test writes its table to the job summary.

### Acceptance
| Criterion | Result |
|---|---|
| 10-question retrieval set reaches hit@5 >= 0.8 | ✅ Grown to 25 harder questions; hit@5 **0.92** (hybrid + rerank), gated in `tests/integration/test_retrieval_quality.py` |
| Every answer carries at least one citation | ✅ Enforced in code (`PolicyAnswer.citations` min 1, verbatim-quote check); integration test over all 25 questions |
| Ingestion is idempotent | ✅ Second run: 0 embedded, 92 unchanged (1.3s); tests cover change/add/remove, model change, dimension change, rollback |
| Lint, types, tests | ✅ ruff, mypy; 160 unit, 44 integration |
| CI green | ⏳ Not pushed yet |

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
