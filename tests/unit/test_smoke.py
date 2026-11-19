"""M0 smoke tests: config loads from env, LLM factory and providers work offline."""

import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from disputedesk.config import Settings
from disputedesk.llm import (
    AnthropicClient,
    BedrockClient,
    FakeClient,
    LLMError,
    LLMTruncatedError,
    Message,
    OllamaClient,
    get_llm,
)
from tests.conftest import make_settings

HI = [Message(role="user", content="hi")]
Handler = Callable[[httpx2.Request], httpx2.Response]


def mock_http(handler: Handler, base_url: str = "http://test") -> httpx2.Client:
    return httpx2.Client(base_url=base_url, transport=httpx2.MockTransport(handler))


# ---------------------------------------------------------------- config / factory


def test_settings_read_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    assert Settings(_env_file=None).llm_provider == "ollama"  # type: ignore[call-arg]


def test_default_provider_is_fake() -> None:
    assert make_settings().llm_provider == "fake"


def test_fake_llm_is_deterministic() -> None:
    llm = get_llm(make_settings(llm_provider="fake"))
    assert isinstance(llm, FakeClient)

    msgs = [Message(role="user", content="My card was charged twice.")]
    first = llm.complete(system="triage", messages=msgs)
    second = llm.complete(system="triage", messages=msgs)
    other = llm.complete(system="triage", messages=[Message(role="user", content="different")])

    assert first.text == second.text
    assert first.text != other.text
    assert first.provider == "fake"


def test_bedrock_is_stubbed() -> None:
    llm = get_llm(make_settings(llm_provider="bedrock"))
    assert isinstance(llm, BedrockClient)
    with pytest.raises(NotImplementedError):
        llm.complete(system="s", messages=HI)


# ---------------------------------------------------------------- anthropic


def anthropic_message(stop_reason: str = "end_turn", text: str = "hello") -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 3, "output_tokens": 1},
    }


def anthropic_client(handler: Handler) -> AnthropicClient:
    settings = make_settings(anthropic_api_key="test-key", llm_max_retries=0)
    return AnthropicClient(settings, http=mock_http(handler, "https://api.anthropic.com"))


def test_anthropic_request_shape_and_parsing() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, json=anthropic_message())

    resp = anthropic_client(handler).complete(system="sys", messages=HI)

    assert seen["path"] == "/v1/messages"
    assert seen["body"]["model"] == "claude-opus-5-5"
    assert seen["body"]["system"] == "sys"
    assert seen["body"]["fallbacks"] == "default"
    assert resp.text == "hello"
    assert resp.input_tokens == 3


def test_anthropic_api_error_becomes_llm_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            401, json={"type": "error", "error": {"type": "authentication_error", "message": "x"}}
        )

    with pytest.raises(LLMError, match="anthropic request failed"):
        anthropic_client(handler).complete(system="s", messages=HI)


def test_anthropic_max_tokens_raises_truncated() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=anthropic_message("max_tokens", '{"decision": "ap'))

    with pytest.raises(LLMTruncatedError):
        anthropic_client(handler).complete(system="s", messages=HI)


# ---------------------------------------------------------------- ollama


def ollama_client(handler: Handler) -> OllamaClient:
    return OllamaClient(make_settings(llm_provider="ollama"), http=mock_http(handler))


def ollama_reply(**overrides: Any) -> dict[str, Any]:
    return {
        "model": "llama3.1",
        "message": {"role": "assistant", "content": "ok"},
        "done_reason": "stop",
        "prompt_eval_count": 5,
        "eval_count": 1,
    } | overrides


def test_ollama_parses_chat_response() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/chat"
        return httpx2.Response(200, json=ollama_reply())

    resp = ollama_client(handler).complete(system="s", messages=HI)
    assert resp.text == "ok"
    assert resp.output_tokens == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200, json={"error": "model 'llama3.1' not found"}),
        httpx2.Response(200, text="not json"),
        httpx2.Response(500, text="boom"),
    ],
    ids=["error-body", "non-json", "http-500"],
)
def test_ollama_bad_responses_become_llm_error(response: httpx2.Response) -> None:
    with pytest.raises(LLMError):
        ollama_client(lambda _req: response).complete(system="s", messages=HI)


def test_ollama_length_raises_truncated() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=ollama_reply(done_reason="length"))

    with pytest.raises(LLMTruncatedError):
        ollama_client(handler).complete(system="s", messages=HI)
