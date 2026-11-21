.PHONY: setup seed api ui test

# One-time setup: .env (with a generated JWT secret), infra, dependencies, seed data, policy index.
setup:
	@test -f .env || cp .env.example .env
	@./scripts/ensure_jwt_secret.sh
	docker compose up -d --wait
	uv sync --all-extras --dev --group ui
	$(MAKE) seed
	uv run python -m disputedesk.rag.ingest

# Reload the 40 synthetic disputes (and clear workflow checkpoints). Never touches audit_log.
seed:
	uv run python -m disputedesk.db.seed --reset

api:
	uv run uvicorn disputedesk.api.app:app --factory --port 8000

ui:
	uv run --group ui streamlit run src/disputedesk/ui/app.py

test:
	uv run pytest tests/unit -q
