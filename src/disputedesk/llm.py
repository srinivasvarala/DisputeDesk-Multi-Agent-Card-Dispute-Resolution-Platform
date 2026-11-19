"""LLM provider factory. The ONLY module allowed to import a provider SDK.

Every agent talks to an `LLMClient`. Pick the provider with `LLM_PROVIDER`:
- anthropic: Claude via the Anthropic API (default)
- bedrock:   Claude via Amazon Bedrock (stub until M9)
- ollama:    local models via the Ollama REST API (offline dev)
- fake:      deterministic, no network; used by unit/integration tests and CI
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Literal, Protocol

import httpx2
from pydantic import BaseModel, Field

from disputedesk.config import Settings, get_settings

if TYPE_CHECKING:
    import anthropic
    from anthropic.types.beta import BetaMessageParam


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class LLMResponse(BaseModel):
    text: str
    model: str
    provider: str
    stop_reason: str | None = None
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class LLMError(RuntimeError):
    """Raised for provider failures the caller should treat as a failed LLM call."""


class LLMRefusalError(LLMError):
    """The model declined the request (stop_reason == 'refusal')."""


class LLMClient(Protocol):
    provider: str

    def complete(
        self, *, system: str, messages: Sequence[Message], max_tokens: int = 4096
    ) -> LLMResponse: ...


# --------------------------------------------------------------------------- anthropic


def _to_anthropic_message(m: Message) -> BetaMessageParam:
    if m.role == "user":
        return {"role": "user", "content": m.content}
    return {"role": "assistant", "content": m.content}


class AnthropicClient:
    provider = "anthropic"

    def __init__(self, settings: Settings, http: httpx2.Client | None = None) -> None:
        import anthropic  # lazy: keep SDK import cost out of fake/ollama runs

        api_key = (
            settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
        )
        self._client: anthropic.Anthropic = anthropic.Anthropic(
            api_key=api_key,  # None -> SDK resolves credentials from the environment
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            http_client=http,
        )
        self._model = settings.anthropic_model

    def complete(
        self, *, system: str, messages: Sequence[Message], max_tokens: int = 4096
    ) -> LLMResponse:
        # Server-side refusal fallback: if the model declines, the API retries on a
        # fallback model inside the same call.
        response = self._client.beta.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[_to_anthropic_message(m) for m in messages],
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise LLMRefusalError(f"model refused: {response.stop_details}")
        text = "".join(block.text for block in response.content if block.type == "text")
        return LLMResponse(
            text=text,
            model=response.model,
            provider=self.provider,
            stop_reason=response.stop_reason,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


# --------------------------------------------------------------------------- bedrock


class BedrockClient:
    provider = "bedrock"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def complete(
        self, *, system: str, messages: Sequence[Message], max_tokens: int = 4096
    ) -> LLMResponse:
        raise NotImplementedError("Bedrock provider is implemented in M9 (AWS phase).")


# --------------------------------------------------------------------------- ollama


class OllamaClient:
    provider = "ollama"

    def __init__(self, settings: Settings, http: httpx2.Client | None = None) -> None:
        self._model = settings.ollama_model
        self._http = http or httpx2.Client(
            base_url=settings.ollama_base_url, timeout=settings.llm_timeout_seconds
        )

    def complete(
        self, *, system: str, messages: Sequence[Message], max_tokens: int = 4096
    ) -> LLMResponse:
        payload = {
            "model": self._model,
            "stream": False,
            "options": {"num_predict": max_tokens},
            "messages": [{"role": "system", "content": system}]
            + [m.model_dump() for m in messages],
        }
        try:
            resp = self._http.post("/api/chat", json=payload)
            resp.raise_for_status()
        except httpx2.HTTPError as exc:
            raise LLMError(f"ollama request failed: {exc}") from exc
        data = resp.json()
        return LLMResponse(
            text=data["message"]["content"],
            model=data.get("model", self._model),
            provider=self.provider,
            stop_reason=data.get("done_reason"),
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
        )


# --------------------------------------------------------------------------- fake

Responder = Callable[[str, Sequence[Message]], str]


def _default_fake_responder(system: str, messages: Sequence[Message]) -> str:
    digest = hashlib.sha256(
        (system + "\x00" + "\x00".join(f"{m.role}:{m.content}" for m in messages)).encode()
    ).hexdigest()[:12]
    return f"[fake:{digest}]"


class FakeClient:
    """Deterministic offline client. Same input -> same output. Optional custom responder."""

    provider = "fake"

    def __init__(self, responder: Responder | None = None) -> None:
        self._responder = responder or _default_fake_responder
        self.calls: list[tuple[str, list[Message]]] = []

    def complete(
        self, *, system: str, messages: Sequence[Message], max_tokens: int = 4096
    ) -> LLMResponse:
        self.calls.append((system, list(messages)))
        text = self._responder(system, messages)
        return LLMResponse(
            text=text,
            model="fake",
            provider=self.provider,
            stop_reason="end_turn",
            input_tokens=sum(len(m.content.split()) for m in messages),
            output_tokens=len(text.split()),
        )


# --------------------------------------------------------------------------- factory


def get_llm(settings: Settings | None = None) -> LLMClient:
    settings = settings or get_settings()
    match settings.llm_provider:
        case "anthropic":
            return AnthropicClient(settings)
        case "bedrock":
            return BedrockClient(settings)
        case "ollama":
            return OllamaClient(settings)
        case "fake":
            return FakeClient()
