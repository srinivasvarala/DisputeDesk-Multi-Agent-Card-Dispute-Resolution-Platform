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

## Evals

`uv run python evals/run_evals.py --thresholds evals/thresholds.yml` runs 40 synthetic disputes (10
adversarial) through the real graph and exits non-zero if any threshold fails. Needs a real model and
the rulebook ingested (`uv sync --all-extras --dev --group evals`, `uv run python -m disputedesk.rag.ingest`).
A full run takes about 20 minutes.

Latest result (Haiku 5.5 as system and judge, 2026-11-20). **The gate is red**: the system is not good
enough yet, and the thresholds were fixed before the first run. Details, causes and what changed
between runs are in [PROGRESS.md](PROGRESS.md#m7-evals--the-harness-is-done-the-gate-is-red-because-the-system-is-not-good-enough-yet).

| Metric | Baseline | Now | Threshold |
|---|---|---|---|
| Router accuracy | 1.000 | 1.000 | ≥ 0.90 |
| Decision accuracy (strict) | 0.133 | 0.267 | ≥ 0.80 |
| Human-approval routing accuracy | 1.000 | 1.000 | ≥ 1.00 |
| Citation hit rate | 0.133 | 0.967 | ≥ 0.70 |
| Citation faithfulness (DeepEval) | 0.952 | 0.982 (39 of 40 scored) | ≥ 0.85 |
| Letter quality (LLM judge, 1-5) | 3.23 | 3.61 | ≥ 4.00 |
| Guardrail catch rate | 0.333 | 0.333 | ≥ 0.80 |
| Guardrail false-positive rate | 0.033 | 0.033 | ≤ 0.05 |
| Injection pass rate | 0.400 | 0.900 | ≥ 1.00 |
| Cases that crashed | 20 of 40 | 0 of 40 | |

Red-team: `uv run npx promptfoo@0.126.0 eval -c evals/promptfoo/redteam.yaml --no-cache` (14 of 18 pass, up from 4).

## Development

```bash
uv run ruff check . && uv run ruff format .
uv run mypy src
uv run pytest tests/unit -q
uv run pre-commit install        # optional: run the same checks on every commit
```

## Architecture decisions

- [ADR-001: Stack choices](docs/adr/001-stack-choices.md)
