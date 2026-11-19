# DisputeDesk progress

Milestone status against [PROJECT_BLUEPRINT.md](PROJECT_BLUEPRINT.md). Updated at the end of each milestone.

| Milestone | Status | Completed | Commits |
|---|---|---|---|
| M0: Scaffold | ✅ Done | 2026-11-19 | `e473ea0`, `c1098da` |
| M1: Data and MCP servers | ✅ Done (CI pending push) | 2026-11-19 | `5e295cf`, `27ecb5b`, `3b7fdb4`, `7850a87`, `d4d52e9` |
| M2: RAG over dispute policies | ⏳ Not started | | |
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
| CI green | ⏳ not pushed yet |

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

