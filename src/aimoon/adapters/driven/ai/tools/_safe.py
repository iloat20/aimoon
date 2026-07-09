"""Shared decorator unifying the 6 pipeline tools' broad-tolerance fallback.

Every tool follows the same failure contract: on bad/missing input it returns
an explicit ``{"__partial__": "<reason>"}`` dict; on any *unexpected* exception
it must never propagate — it should degrade to a partial dict so the upstream
orchestrator can continue.  This decorator removes the duplicated
``try/except`` + ``logger.debug`` boilerplate from each tool's ``run``.
"""

from __future__ import annotations

import functools
import logging

logger = logging.getLogger(__name__)


def tool_safe(default_partial: str = "computation_error"):
    """Wrap a tool ``run`` so any uncaught exception degrades to a partial dict.

    The wrapped function's own explicit ``{"__partial__": ...}`` early returns
    (missing inputs, insufficient data) are preserved verbatim — only truly
    unexpected exceptions are caught here and logged at debug level.
    """

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as e:  # noqa: BLE001 — tools must never raise
                logger.debug("[%s] partial: %s: %s", fn.__module__, type(e).__name__, e)
                return {"__partial__": default_partial}

        return wrapper

    return deco
