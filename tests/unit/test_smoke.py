"""M0 smoke test: package imports, config loads from env, LLM factory works offline."""

import json

import httpx2
import pytest

from disputedesk.config import Settings
from disputedesk.llm import (
    AnthropicClient,
    BedrockClient,
    FakeClient,
    Message,
    OllamaClient,
    get_llm,
)


@pytest.fixture
def fake_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_settings_read_env(fake_settings: Settings) -> None:
    assert fake_settings.llm_provider == "fake"


def test_fake_llm_is_deterministic(fake_settings: Settings) -> None:
    llm = get_llm(fake_settings)
    assert isinstance(llm, FakeClient)

    msgs = [Message(role="user", content="My card was charged twice.")]
    first = llm.complete(system="triage", messages=msgs)
    second = llm.complete(system="triage", messages=msgs)
    other = llm.complete(system="triage", messages=[Message(role="user", content="different")])

    assert first.text == second.text
    assert first.text != other.text
    assert first.provider == "fake"


def test_bedrock_is_stubbed() -> None:
    llm = get_llm(Settings(_env_file=None, llm_provider="bedrock"))  # type: ignore[call-arg]
    assert isinstance(llm, BedrockClient)
    with pytest.raises(NotImplementedError):
        llm.complete(system="s", messages=[Message(role="user", content="hi")])


def test_anthropic_request_shape_and_parsing() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx2.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [{"type": "text", "text": "hello"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 3, "output_tokens": 1},
            },
        )

    settings = Settings(_env_file=None, anthropic_api_key="test-key")  # type: ignore[call-arg]
    http = httpx2.Client(transport=httpx2.MockTransport(handler))
    llm = AnthropicClient(settings, http=http)
    resp = llm.complete(system="sys", messages=[Message(role="user", content="hi")])

    body = seen["body"]
    assert isinstance(body, dict)
    assert seen["path"] == "/v1/messages"
    assert body["model"] == "claude-opus-5-5"
    assert body["system"] == "sys"
    assert body["fallbacks"] == "default"
    assert resp.text == "hello"
    assert resp.input_tokens == 3


def test_ollama_parses_chat_response() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/chat"
        return httpx2.Response(
            200,
            json={
                "model": "llama3.1",
                "message": {"role": "assistant", "content": "ok"},
                "done_reason": "stop",
                "prompt_eval_count": 5,
                "eval_count": 1,
            },
        )

    http = httpx2.Client(base_url="http://ollama.test", transport=httpx2.MockTransport(handler))
    llm = OllamaClient(Settings(_env_file=None, llm_provider="ollama"), http=http)  # type: ignore[call-arg]
    resp = llm.complete(system="s", messages=[Message(role="user", content="hi")])
    assert resp.text == "ok"
    assert resp.output_tokens == 1
