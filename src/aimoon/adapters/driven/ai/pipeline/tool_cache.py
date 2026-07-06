"""Tool-result shared cache(built on DiskTtlCache) with short TTL.

Purpose:
    Same-symbol analyses often use identical quote/financial/chain datav1). Rather
    than re-computing 6 pure pandas/numpy tools on every run, we cache the tool
    outputs by a content hash of the upstream ``StockAnalysis`` aggregate. The
    TTL is short (60s by default) because freshness matters for stock data, but
    catches rapid re-runs(V/O test that runs 6 modules on the same symbol, or
    users pressing <up><enter> in their terminal).

Why not use the existing ``ai/cache.py``(24h TTL on final report)? That cache
misses every time market data changes. Our cache keys on the *input* hash, so
it hits only when the upstream data is byte-identical  —  guaranteed fresh.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from aimoon.adapters.driven.common.cache import DiskTtlCache

logger = logging.getLogger(__name__)

_tool_cache = DiskTtlCache(namespace="v2_tool_outputs", ttl_seconds=60)


def _fingerprint(si: Any) -> str:
    """Build a short hash key from the upstream StockAnalysis fields that feed tools.

    We don't hash the whole ``si`` (slow, includes long social posts text); only
    OHLCV bar count + financial symbols + capital flow numbers —  the inputs to
    the 6 pure tools. If any of these change, the cache misses (fresh).
    """
    k = {
        "symbol": getattr(si, "symbol", ""),
        "name": getattr(si, "name", ""),
        "bar_count": len(getattr(getattr(si, "kline", None), "bars") or []),
        "close": (getattr(getattr(si, "kline", None), "bars") or [])[-1].close
        if getattr(getattr(si, "kline", None), "bars") else None,
        "rev": round(getattr(getattr(si, "financial", None), "revenue") or 0, 2),
        "main_net_5d": round(
            getattr(getattr(si, "capital_flow", None), "main_net_5d") or 0, 2
        ),
        "history_count": len(getattr(si, "history_financial") or []),
    }
    blob = json.dumps(k, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def get_cached_tool_results(si: Any) -> dict[str, Any] | None:
    """Retrieve cached tool outputs for *si* if fresh enough."""
    key = _fingerprint(si)
    hit = _tool_cache.get(key)
    if hit is None:
        return None
    logger.debug("[tool_cache] HIT key=%s (%d tools)", key, len(hit))
    return hit


def set_cached_tool_results(si: Any, results: dict[str, Any]) -> None:
    """Persist tool outputs keyed by *si* fingerprint."""
    key = _fingerprint(si)
    try:
        _tool_cache.set(key, results)
        logger.debug("[tool_cache] SET key=%s (%d tools)", key, len(results))
    except Exception:
        pass  # broad tolerance: cache write failures must not break the pipeline
