import threading
import time

import pytest

from disputedesk.agents.resilience import (
    CallFailedError,
    CallTimeoutError,
    NonRetryableError,
    ResilientLLM,
    call_with_retry,
)
from disputedesk.llm import FakeClient, LLMError, LLMRefusalError, LLMResponse, Message

MSG = [Message(role="user", content="hi")]


def no_sleep(_: float) -> None:
    return None


def test_returns_first_success() -> None:
    assert call_with_retry(lambda: 7, name="x", timeout=1, retries=2, sleep=no_sleep) == 7


def test_retries_then_succeeds() -> None:
    calls = []

    def flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("boom")
        return "ok"

    assert call_with_retry(flaky, name="x", timeout=1, retries=2, sleep=no_sleep) == "ok"
    assert len(calls) == 3


def test_gives_up_after_all_attempts() -> None:
    calls = []

    def always() -> None:
        calls.append(1)
        raise RuntimeError("boom")

    with pytest.raises(CallFailedError, match="after 3 attempts"):
        call_with_retry(always, name="x", timeout=1, retries=2, sleep=no_sleep)
    assert len(calls) == 3


def test_non_retryable_stops_at_once() -> None:
    calls = []

    class Bad(NonRetryableError):
        pass

    def bad() -> None:
        calls.append(1)
        raise Bad("nope")

    with pytest.raises(CallFailedError, match="nope"):
        call_with_retry(bad, name="x", timeout=1, retries=5, sleep=no_sleep)
    assert len(calls) == 1


def test_timeout_is_enforced_and_retried() -> None:
    release = threading.Event()
    calls = []

    def hang() -> None:
        calls.append(1)
        release.wait(5)

    started = time.perf_counter()
    with pytest.raises(CallTimeoutError):
        call_with_retry(hang, name="x", timeout=0.05, retries=1, sleep=no_sleep)
    release.set()
    assert len(calls) == 2
    assert time.perf_counter() - started < 2


def test_backoff_doubles() -> None:
    delays: list[float] = []

    def boom() -> None:
        raise RuntimeError("x")

    with pytest.raises(CallFailedError):
        call_with_retry(boom, name="x", timeout=1, retries=3, backoff=0.1, sleep=delays.append)
    assert delays == [0.1, 0.2, 0.4]


class FlakyLLM:
    provider = "flaky"

    def __init__(self, failures: int, error: Exception | None = None) -> None:
        self.failures = failures
        self.error = error or LLMError("503")
        self.calls = 0

    def complete(self, *, system: str, messages: object, max_tokens: int = 4096) -> LLMResponse:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return LLMResponse(text="fine", model="m", provider="flaky")


def test_resilient_llm_retries_transient_errors() -> None:
    inner = FlakyLLM(failures=2)
    llm = ResilientLLM(inner, timeout=1, retries=2)
    assert llm.complete(system="s", messages=MSG).text == "fine"
    assert inner.calls == 3
    assert llm.provider == "flaky"


def test_resilient_llm_does_not_retry_a_refusal() -> None:
    inner = FlakyLLM(failures=5, error=LLMRefusalError("no"))
    with pytest.raises(LLMError):
        ResilientLLM(inner, timeout=1, retries=2).complete(system="s", messages=MSG)
    assert inner.calls == 1


def test_resilient_llm_passes_through_the_fake_client() -> None:
    assert ResilientLLM(FakeClient(), timeout=1, retries=0).complete(system="s", messages=MSG)
