"""Retry helper for connector calls."""

from __future__ import annotations

import time


def call_with_retry(fn, attempts: int = 3, retry_on: tuple[type[BaseException], ...] = (TimeoutError, ConnectionError), backoff: tuple[float, ...] = (0.0, 0.2, 0.5)):
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except retry_on as exc:
            last = exc
            if attempt >= attempts - 1:
                break
            delay = backoff[min(attempt, len(backoff) - 1)]
            if delay:
                time.sleep(delay)
    assert last is not None
    raise last
