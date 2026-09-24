"""Minimal retry-with-backoff helper.

Written in-house instead of pulling in `tenacity`: the only thing the broker
adapter needs is "retry N times, with exponential backoff, only for specific
exception types" — about a dozen lines. Adding a dependency for that would
violate the project's own "never add dependencies without justification"
rule (Phase 18 / CLAUDE.md). If retry needs grow more elaborate later
(jitter strategies, per-call budgets, circuit breaking), revisit and
document that decision in DECISIONS.md before reaching for a library.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import TypeVar

T = TypeVar("T")


def retry_call(
    fn: Callable[[], T],
    *,
    retry_on: tuple[type[BaseException], ...] | type[BaseException],
    max_attempts: int = 4,
    base_delay_seconds: float = 1.0,
    max_delay_seconds: float = 10.0,
    sleep: Callable[[float], None] | None = None,
) -> T:
    """Call `fn()`, retrying with exponential backoff if it raises one of
    `retry_on`. Re-raises the last exception once `max_attempts` is used up.

    `sleep` defaults to `time.sleep`, looked up at call time (not bound as a
    default-argument value) so tests can `monkeypatch.setattr(time, "sleep",
    ...)` and have it actually take effect.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    sleep_fn = sleep if sleep is not None else time.sleep

    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except retry_on:
            if attempt >= max_attempts:
                raise
            delay = min(base_delay_seconds * (2 ** (attempt - 1)), max_delay_seconds)
            sleep_fn(delay)
