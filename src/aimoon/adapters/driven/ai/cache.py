"""AI analysis cache using DiskTtlCache."""

from __future__ import annotations

from datetime import datetime

from aimoon.adapters.driven.common.cache import DiskTtlCache

_cache = DiskTtlCache(namespace="ai_analysis", ttl_seconds=86400)


def get_analysis_cache(symbol: str) -> str | None:
    """Get cached analysis for symbol if fresh enough."""
    today = datetime.now().strftime("%Y%m%d")
    key = f"analysis:{symbol}:{today}"
    # 直接读取缓存文件检查 TTL
    path = _cache._path_for(key)
    if not path.exists():
        return None
    try:
        import json
        import time
        raw = json.loads(path.read_text(encoding="utf-8"))
        ts = raw.get("ts", 0)
        ttl = raw.get("data", {}).get("ttl", _cache.ttl_seconds)
        if time.time() - ts > ttl:
            path.unlink(missing_ok=True)
            return None
        return raw.get("data", {}).get("report_text")
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def set_analysis_cache(symbol: str, report_text: str) -> None:
    """Cache analysis result."""
    today = datetime.now().strftime("%Y%m%d")
    key = f"analysis:{symbol}:{today}"
    # 财报季缩短 TTL
    month = datetime.now().month
    ttl = 21600 if month in (1, 4, 7, 10) else 86400
    _cache.set(key, {"report_text": report_text, "ttl": ttl})
