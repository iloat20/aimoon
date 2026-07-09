"""Per-phase timing instrumentation — shared across adapters.

Lightweight context manager that logs wall-clock seconds per phase via the
standard ``logging`` module (logger name ``aimoon.pipeline.timing``).
No-op when logging is disabled; zero allocation on the hot path beyond a
``time.monotonic()`` sample.

Lives in ``common/`` so both ``collectors/`` and ``ai/`` can depend on it
without crossing adapter boundaries (audit P2.3).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Generator
from contextlib import contextmanager

logger = logging.getLogger("aimoon.pipeline.timing")


@contextmanager
def logphase(label: str) -> Generator[None, None, None]:
    """Emit a DEBUG line with elapsed seconds when ``label`` block exits.

    Usage::

        with logphase("ANALYSIS llm"):
            draft = await call(...)
    """
    t0 = time.monotonic()
    try:
        yield
    finally:
        elapsed = time.monotonic() - t0
        logger.debug("[pipeline][timing] %s %.2fs", label, elapsed)
        # 同时打到主 logger.info,方便在不开启 DEBUG 时也能看到耗时
        logging.getLogger("aimoon.pipeline").info(
            "[pipeline][timing] %s %.2fs", label, elapsed
        )
