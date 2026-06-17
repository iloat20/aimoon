"""RPS（相对价格强度）计算 — 纯信息展示，不参与评分。"""

from __future__ import annotations

import pandas as pd

from aimoon.models import ScoredStock


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
        rank_maps[f"rps{n}"] = {
            code: (rank + 1) / total * 100 for rank, code in enumerate(sorted_codes)
        }
    updated: list[ScoredStock] = []
    for r in results:
        rps_vals = {key: rank_maps[key][r.code] for key in rank_maps if r.code in rank_maps[key]}
        updated.append(
            ScoredStock(
                code=r.code,
                name=r.name,
                price=r.price,
                pct_change=r.pct_change,
                turnover=r.turnover,
                pe=r.pe,
                pb=r.pb,
                market_cap_yi=r.market_cap_yi,
                signals=(),
                rps=rps_vals,
                ml_score=r.ml_score,
                total_score=r.total_score,
            )
        )
    return updated
