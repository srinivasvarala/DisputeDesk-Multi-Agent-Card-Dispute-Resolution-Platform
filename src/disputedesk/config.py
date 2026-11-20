"""Application settings, loaded from environment variables (and `.env` for local dev)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["anthropic", "bedrock", "ollama", "fake"]
EmbeddingProvider = Literal["fastembed", "fake"]
RerankerProvider = Literal["fastembed", "fake", "none"]


class Settings(BaseSettings):
    # .env is resolved from the current directory, so run commands from the repo root.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- LLM ---
    # Default "fake" so a missing or unloaded .env can never trigger paid API calls.
    llm_provider: LLMProvider = "fake"
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2

    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-opus-5-5"

    bedrock_region: str = "us-east-1"
    bedrock_model: str = "anthropic.claude-opus-5-5"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"

    # --- RAG ---
    # Local ONNX models via fastembed (free, no API key). Weights download on first use into
    # model_cache_dir. "fake" providers are deterministic and offline, for unit tests.
    embedding_provider: EmbeddingProvider = "fastembed"
    embedding_model: str = "BAAI/bge-base-en-v1.5"
    # Instruction prepended to queries (never documents). None = the model default: the bge
    # instruction for BAAI/bge-* models, nothing for others. Set "" to force no prefix.
    embedding_query_prefix: str | None = None
    reranker_provider: RerankerProvider = "fastembed"  # "none" disables reranking
    # Best of 4 fastembed rerankers on evals/datasets/retrieval.jsonl (see PROGRESS.md, M2).
    reranker_model: str = "BAAI/bge-reranker-v2-m3-int8"
    model_cache_dir: str = ".cache/models"
    policies_dir: str = "data/policies"

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
