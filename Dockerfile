# API image. Built by security.yml so Trivy can scan it; not yet used to deploy (M9).
FROM python:3.12-slim

# Pick up Debian security fixes released after the base image was built.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so this layer is cached until the lockfile changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY data ./data
RUN uv sync --frozen --no-dev

RUN useradd --system --uid 10001 --no-create-home app \
 && mkdir -p /app/.cache && chown app /app/.cache
USER app

ENV PATH="/app/.venv/bin:$PATH" \
    MODEL_CACHE_DIR=/app/.cache/models

EXPOSE 8000
# JWT_SECRET and DATABASE_URL must come from the environment; there are no defaults in the image.
CMD ["uvicorn", "disputedesk.api.app:app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
