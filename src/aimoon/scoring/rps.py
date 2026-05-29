"""RPS（相对价格强度）计算"""
from __future__ import annotations
import pandas as pd
from aimoon.models import Signal, ScoredStock


def compute_rps(results: list[ScoredStock], tails: dict[str, pd.DataFrame]) -> list[ScoredStock]:
    """计算 RPS 并返回新的 ScoredStock 列表（不可变更新）。"""
    if not results:
        return results
    returns: dict[int, dict[str, float]] = {5: {}, 10: {}, 15: {}, 20: {}}
    for r in results:
        tail = tails.get(r.code)
        if tail is None or len(tail) < 21:
            continue
        close = pd.to_numeric(tail["close"], errors="coerce")
        for n in returns:
            if len(close) > n:
                prev = close.iloc[-n - 1]
                if prev > 0:
                    returns[n][r.code] = (close.iloc[-1] - prev) / prev * 100
    rank_maps: dict[str, dict[str, float]] = {}
    for n, ret_map in returns.items():
        if not ret_map:
            continue
        sorted_codes = sorted(ret_map, key=lambda c: ret_map[c])
        total = len(sorted_codes)
        rank_maps[f"rps{n}"] = {code: (rank + 1) / total * 100 for rank, code in enumerate(sorted_codes)}
    updated: list[ScoredStock] = []
    for r in results:
        rps = {key: rank_maps[key][r.code] for key in rank_maps if r.code in rank_maps[key]}
        rps_red = sum(1 for v in rps.values() if v > 90)
        rps_signals = list(r.signals)
        if rps_red >= 3:
            rps_signals.append(Signal("rps_triple", f"RPS三线翻红({rps_red}/4)", +5))
        elif rps_red >= 2:
            rps_signals.append(Signal("rps_double", f"RPS双线红({rps_red}/4)", +3))
        updated.append(ScoredStock(
            code=r.code, name=r.name, price=r.price,
            pct_change=r.pct_change, turnover=r.turnover,
            pe=r.pe, pb=r.pb, market_cap_yi=r.market_cap_yi,
            signals=tuple(rps_signals), rps=rps,
        ))
    return updated
