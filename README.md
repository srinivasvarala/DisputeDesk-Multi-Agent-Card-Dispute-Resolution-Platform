# DisputeDesk

Multi-agent card-dispute resolution with MCP tools, RAG, guardrails, observability and evals built in.
LangGraph + FastAPI, runs locally, deploys to AWS.

> Portfolio project. All data is synthetic. See [PROJECT_BLUEPRINT.md](PROJECT_BLUEPRINT.md) for the
> architecture and milestones.

## Status

See [PROGRESS.md](PROGRESS.md) for milestone status and details.

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```bash
cp .env.example .env
docker compose up -d --wait      # Postgres+pgvector, Langfuse (:3000), OTel collector (:4317/4318)
uv sync --all-extras --dev
uv run pytest -q
```

`LLM_PROVIDER=fake` (the default in `.env.example`) needs no API key. Set `LLM_PROVIDER=anthropic` and
`ANTHROPIC_API_KEY` to use Claude, or `LLM_PROVIDER=ollama` for a local model.

## Development

```bash
uv run ruff check . && uv run ruff format .
uv run mypy src
uv run pytest tests/unit -q
uv run pre-commit install        # optional: run the same checks on every commit
```

## Architecture decisions

- [ADR-001: Stack choices](docs/adr/001-stack-choices.md)
