"""Retry logic and silent failure context manager for infrastructure operations."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager

_SILENT_NETWORK_ERRORS = (ConnectionError, TimeoutError, OSError)


@contextmanager
def silent_failure(context: str, default_return=None):
    try:
        yield
    except Exception as e:
        if isinstance(e, _SILENT_NETWORK_ERRORS):
            logging.debug("[%s] %s: %s", context, type(e).__name__, e)
        else:
            logging.warning("[%s] %s: %s", context, type(e).__name__, e)


def retry_on_connection(func, *args, retries: int = 2, delay: float = 1.0, **kwargs):
    """Call *func* with retries on transient connection errors."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return func(*args, **kwargs)
        except (
            ConnectionError,
            ConnectionAbortedError,
            TimeoutError,
            OSError,
        ) as exc:
            last_exc = exc
            if attempt < retries:
                logging.debug(
                    "[retry] %s attempt %d/%d failed: %s",
                    func.__qualname__,
                    attempt + 1,
                    retries,
                    exc,
                )
                time.sleep(delay * (attempt + 1))
    assert last_exc is not None
    raise last_exc
