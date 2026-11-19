"""Application settings, loaded from environment variables (and `.env` for local dev)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["anthropic", "bedrock", "ollama", "fake"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- LLM ---
    llm_provider: LLMProvider = "anthropic"
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2

    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-opus-5-5"

    bedrock_region: str = "us-east-1"
    bedrock_model: str = "anthropic.claude-opus-5-5"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"

    # --- Data ---
    database_url: str = "postgresql://postgres:postgres@localhost:5432/disputedesk"

    # --- Observability ---
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    otel_service_name: str = "disputedesk"
    langfuse_host: str = "http://localhost:3000"
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    """Return process-wide settings. Call `get_settings.cache_clear()` in tests to reload."""
    return Settings()
