"""Timeouts and retries for every LLM and tool call.

`call_with_retry` runs a callable on a worker thread so it can be abandoned after `timeout`
seconds (the thread itself cannot be killed; it is left to finish and its result dropped).
Failures marked non-retryable (a refusal, a bad request) are raised at once, others are
retried with a short exponential backoff.

`ResilientLLM` wraps any `LLMClient` so agents get this behaviour without knowing about it.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from disputedesk.llm import (
    LLMClient,
    LLMError,
    LLMRefusalError,
    LLMResponse,
    LLMTruncatedError,
    Message,
)


class CallFailedError(RuntimeError):
    """A call still failed after all attempts (or failed in a way retrying cannot fix)."""


class CallTimeoutError(CallFailedError):
    pass


class NonRetryableError(Exception):
    """Mix-in marker: raising a subclass skips the remaining retries."""


def call_with_retry[T](
    fn: Callable[[], T],
    *,
    name: str,
    timeout: float,
    retries: int,
    backoff: float = 0.25,
    sleep: Callable[[float], None] = time.sleep,
    non_retryable: tuple[type[BaseException], ...] = (NonRetryableError,),
) -> T:
    """Call `fn` up to `retries + 1` times, each attempt limited to `timeout` seconds."""
    attempts = retries + 1
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=name)
        future = pool.submit(fn)
        try:
            return future.result(timeout=timeout)
        except FutureTimeout:
            last = CallTimeoutError(f"{name} timed out after {timeout}s")
        except non_retryable as exc:
            raise CallFailedError(f"{name} failed: {exc}") from exc
        except Exception as exc:
            last = exc
        finally:
            pool.shutdown(wait=False)  # never block on a hung call
        if attempt < attempts:
            sleep(backoff * 2 ** (attempt - 1))
    if isinstance(last, CallTimeoutError):
        raise CallTimeoutError(f"{last} (attempts: {attempts})") from last
    raise CallFailedError(f"{name} failed after {attempts} attempts: {last}") from last


class ResilientLLM:
    """An `LLMClient` that adds a timeout and retries to another `LLMClient`."""

    def __init__(self, inner: LLMClient, *, timeout: float, retries: int) -> None:
        self._inner = inner
        self._timeout = timeout
        self._retries = retries
        self.provider = inner.provider

    def complete(
        self, *, system: str, messages: Sequence[Message], max_tokens: int = 4096
    ) -> LLMResponse:
        try:
            return call_with_retry(
                lambda: self._inner.complete(
                    system=system, messages=messages, max_tokens=max_tokens
                ),
                name="llm",
                timeout=self._timeout,
                retries=self._retries,
                non_retryable=(LLMRefusalError, LLMTruncatedError),
            )
        except CallFailedError as exc:
            raise LLMError(str(exc)) from exc
