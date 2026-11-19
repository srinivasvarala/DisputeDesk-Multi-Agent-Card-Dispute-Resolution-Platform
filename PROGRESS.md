# DisputeDesk progress

Milestone status against [PROJECT_BLUEPRINT.md](PROJECT_BLUEPRINT.md). Updated at the end of each milestone.

| Milestone | Status | Completed | Commits |
|---|---|---|---|
| M0: Scaffold | ✅ Done | 2026-11-19 | `e473ea0`, `c1098da` |
| M1: Data and MCP servers | ⏳ Not started | | |
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
| CI green on first push | ⏸️ Not verified: no remote pushed yet |

### Decisions
- Langfuse v2 instead of v3 (v3 needs ClickHouse, Redis, MinIO). See ADR-001.
- `httpx2` instead of `httpx`: same HTTP stack as the Anthropic SDK 2.x.
- CI steps for RAG ingest and integration tests are commented out until M2/M3.

### Known gaps / carried forward
- OTel collector healthcheck only validates config (distroless image); live check is `http://localhost:13133/`.
- `OllamaClient` does not close its HTTP client. Address in M3 when client lifecycle is decided.
- Langfuse v2 has no OTLP ingest; LLM traces go via the Langfuse SDK in M6.
