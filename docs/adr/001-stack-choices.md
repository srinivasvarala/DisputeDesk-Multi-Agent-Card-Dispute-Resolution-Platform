# ADR-001: Stack choices

- Status: accepted
- Date: 2026-11-19

## Context

DisputeDesk is a portfolio project that has to show guardrails, observability, evals, security, MCP and RAG
working together, built in a day or two and runnable locally with one `docker compose up`.

## Decision

| Area | Choice | Why |
|---|---|---|
| Language / packaging | Python 3.12, uv | Fast, reproducible installs with a lockfile; one tool for venv + deps + Python |
| Lint / format / types | ruff, mypy (strict) | ruff replaces flake8/isort/black; strict mypy catches boundary bugs early |
| Data validation | Pydantic v2 + pydantic-settings | One model layer for API, agent state, tool schemas and env config |
| LLM access | Single `llm.py` factory: anthropic (default), bedrock (M9), ollama, fake | Swap providers via `LLM_PROVIDER`; `fake` keeps tests and CI offline and deterministic |
| HTTP client | `httpx2` | Same client the Anthropic SDK 2.x uses, so one HTTP stack and one mock transport for tests |
| Database | Postgres 16 + pgvector | One store for cases, audit log and embeddings; hybrid search with tsvector in the same DB |
| Tracing | OpenTelemetry collector | Vendor-neutral; the same instrumentation works with Langfuse locally and ADOT/CloudWatch on AWS |
| LLM observability | Langfuse **v2**, self-hosted | v2 needs only Postgres (2 containers). v3 needs ClickHouse, Redis and S3/MinIO (6+ containers), which is too heavy for a laptop demo |
| CI | GitHub Actions | Lint, types and tests on every push; evals and security in separate workflows |

## Consequences

- Langfuse v2 does not accept OTLP directly, so in M6 LLM traces go to Langfuse through its SDK, and the
  collector handles service-level traces. Migrating to Langfuse v3 later would let the collector export
  OTLP to Langfuse and unify both.
- The OTel collector image is distroless, so its compose healthcheck validates config with the collector
  binary; the live health endpoint is `http://localhost:13133/`.
- Bedrock is a stub until M9. No `boto3` dependency until then.

**M6 outcome:** implemented as described. Spans go to the collector (then Jaeger); LLM generations go through the
Langfuse v2 SDK with the OTel trace id as the Langfuse trace id, so one id finds the case in both UIs.
