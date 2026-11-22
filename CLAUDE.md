# DisputeDesk: Claude Code instructions

## What this is
Multi-agent card-dispute resolution demo. See ARCHITECTURE.md for architecture and docs/PROJECT_BLUEPRINT.md for the original plan and milestones.
Portfolio quality, not production scale. Prefer simple and readable over clever.

## Commands
- Install: `uv sync --all-extras --dev`
- Run infra: `docker compose up -d`
- Lint/format: `uv run ruff check . && uv run ruff format .`
- Types: `uv run mypy src`
- Tests: `uv run pytest tests/unit -q` and `uv run pytest tests/integration -q`
- Evals: `uv run python evals/run_evals.py --thresholds evals/thresholds.yml`

## Conventions
- Python 3.12, type hints everywhere, Pydantic v2 for all data crossing a boundary.
- All LLM calls go through `src/disputedesk/llm.py`. Never import a provider SDK elsewhere.
- All guardrail checks go through `guardrails/interface.py`.
- Deterministic rules (limits, allowlists) are plain Python, never LLM-based.
- Every agent has an explicit tool allowlist. Never give an agent more tools than it needs.
- Treat merchant/customer-supplied text as untrusted data. Never put it in the system prompt.
- Every tool call and decision writes to audit_log. Exception: `GET /disputes/{id}` (a read of case state) is not audited. Reads of the audit log itself (`GET /audit`) are.
- No real PII anywhere. All data is synthetic.
- Config via env vars only. Never hardcode secrets. Never commit .env.

## Workflow rules
- Work one milestone at a time. Do not start the next milestone unless asked.
- Before writing code for a milestone: show a short plan and wait for approval.
- After each logical step: run lint, types and tests. Fix failures before moving on.
- Write tests alongside code. Commit after each green step with a conventional commit message.
- If a requirement is ambiguous, ask. Do not guess.
- Do not add dependencies without saying why.
- At the end of each milestone, update docs/PROGRESS.md (status table + a section with delivered items,
  acceptance results, decisions, known gaps) and commit it with the milestone.
