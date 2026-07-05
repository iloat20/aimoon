"""L2 阶段级内存缓存 (进程生命周期)。

Key = f"{symbol}:{data_fingerprint}:{phase}".
数据指纹含行情/财务/资金/K线,行情变化时自动失效。
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis

_cache: dict[str, tuple[float, Any]] = {}


def _fingerprint(si: StockAnalysis) -> str:
    seed = json.dumps(
        {
            "s": si.symbol,
            "p": getattr(si.quote, "price", None),
            "r": getattr(si.financial, "revenue", 0),
            "cf": getattr(si.capital_flow, "main_net_5d", 0),
            "kc": len(getattr(si.kline, "bars", [])),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha1(seed.encode()).hexdigest()[:16]


def cache_key(si: StockAnalysis, phase: str) -> str:
    return f"{si.symbol}:{_fingerprint(si)}:{phase}"


def get_phase_cache(si: StockAnalysis, phase: str) -> Any | None:
    v = _cache.get(cache_key(si, phase))
    return v[1] if v else None


def set_phase_cache(si: StockAnalysis, phase: str, payload: Any) -> None:
    _cache[cache_key(si, phase)] = (time.monotonic(), payload)
