"""AI analysis cache using DiskTtlCache."""

from __future__ import annotations

from datetime import datetime

from aimoon.adapters.driven.common.cache import DiskTtlCache, _quiet_unlink

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
            _quiet_unlink(path)
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


# 骨架缓存: 必须与终稿缓存(analysis:*)使用不同 key。
# 否则 _pipeline_analyze 写入的「终稿全文」会覆盖 _phase_analysis 写入的「JSON 骨架」,
# 导致同标的同日复跑时把上次终稿当骨架、跳过 LLM、解析失败 → 静默降级
# (2026-07-14 修复的 key 碰撞 bug)。


def get_skeleton_cache(symbol: str) -> str | None:
    """Get cached ANALYSIS skeleton JSON text for symbol if fresh enough."""
    today = datetime.now().strftime("%Y%m%d")
    key = f"skeleton:{symbol}:{today}"
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
            _quiet_unlink(path)
            return None
        return raw.get("data", {}).get("skeleton_text")
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def set_skeleton_cache(symbol: str, skeleton_text: str) -> None:
    """Cache ANALYSIS skeleton JSON text (independent key from final report)."""
    today = datetime.now().strftime("%Y%m%d")
    key = f"skeleton:{symbol}:{today}"
    month = datetime.now().month
    ttl = 21600 if month in (1, 4, 7, 10) else 86400
    _cache.set(key, {"skeleton_text": skeleton_text, "ttl": ttl})
