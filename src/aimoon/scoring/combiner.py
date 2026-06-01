"""因子合成 — IC 加权 + 行业中性化"""
from __future__ import annotations

import logging
from collections import defaultdict

from aimoon.models import ScoredStock, Signal

logger = logging.getLogger(__name__)

# Signal name prefix -> scorer name mapping for IC weight lookup.
_SIGNAL_TO_SCORER: dict[str, str] = {
    "roc5_": "score_momentum", "roc10_": "score_momentum", "roc20_": "score_momentum",
    "accel_": "score_momentum", "decel_": "score_momentum",
    "high_": "score_momentum", "low_": "score_momentum", "adx_strong": "score_momentum",
    "roc3_": "score_momentum_ext", "roc40_": "score_momentum_ext",
    "roc60_": "score_momentum_ext", "roc120_": "score_momentum_ext",
    "rps_ext_": "score_momentum_ext",
    "vol_adj_": "score_momentum_ext", "persist_": "score_momentum_ext",
    "skew_": "score_momentum_ext", "ud_vol_": "score_momentum_ext",
    "obv_": "score_momentum_ext", "vwap_": "score_momentum_ext",
    "recovery_": "score_momentum_ext", "hl_net_": "score_momentum_ext",
    "crash_filter": "score_momentum_ext",
    "ma_golden": "score_trend", "ma_death": "score_trend",
    "ma_align_": "score_trend_ext", "above_ma20_60": "score_trend_ext",
    "above_ma20": "score_trend_ext", "below_ma20_60": "score_trend_ext",
    "adx_bull_": "score_trend_ext", "adx_bear_": "score_trend_ext",
    "macd_red_": "score_trend_ext", "macd_green_": "score_trend_ext",
    "ema_slope_": "score_trend_ext",
    "rps_": "score_rps", "rsi_": "score_rsi", "macd_": "score_macd",
    "kdj_": "score_kdj", "volume_": "score_volume",
    "boll_": "score_bollinger", "sector_": "score_sector",
    "reversal_": "score_reversal",
    "alpha_": "score_alpha",  # Alpha Zoo 截面因子
}


def ic_weighted_score(
    scored: ScoredStock,
    ic_weights: dict[str, float],
) -> ScoredStock:
    """用 IC 权重重新计算单只股票的总分，返回新 ScoredStock。"""
    if not ic_weights:
        return scored

    weighted = 0.0
    for s in scored.signals:
        weight = _find_weight(s.name, ic_weights)
        weighted += s.score * weight

    raw = scored.total_score
    diff = weighted - raw
    if abs(diff) < 0.01:
        return scored

    synthetic = Signal("ic_weight_adj", "IC权重调整", int(round(diff)))
    return ScoredStock(
        code=scored.code, name=scored.name, price=scored.price,
        pct_change=scored.pct_change, turnover=scored.turnover,
        pe=scored.pe, pb=scored.pb, market_cap_yi=scored.market_cap_yi,
        signals=scored.signals + (synthetic,), rps=scored.rps,
    )


def ic_weighted_combine(
    scored_list: list[ScoredStock],
    ic_weights: dict[str, float],
) -> list[ScoredStock]:
    """用 IC 权重重新排序股票列表。

    不修改 ScoredStock 本身（total_score 仍为信号求和），
    而是返回按 IC 加权分排序的新列表。
    """
    if not ic_weights:
        return sorted(scored_list, key=lambda s: s.total_score, reverse=True)

    def weighted_score(s: ScoredStock) -> float:
        total = 0.0
        for sig in s.signals:
            weight = _find_weight(sig.name, ic_weights)
            total += sig.score * weight
        return total

    return sorted(scored_list, key=weighted_score, reverse=True)


def industry_neutralize(
    scored_list: list[ScoredStock],
    sector_map: dict[str, str],
    top_per_sector: int = 3,
    max_total: int = 30,
) -> list[ScoredStock]:
    """行业内排名去极值，避免全选同一板块。

    每个板块取 top_per_sector 只，总共取 max_total 只。
    """
    if not sector_map:
        return scored_list[:max_total]

    # 按板块分组
    groups: dict[str, list[ScoredStock]] = defaultdict(list)
    no_sector: list[ScoredStock] = []

    for s in scored_list:
        sector = sector_map.get(s.code)
        if sector:
            groups[sector].append(s)
        else:
            no_sector.append(s)

    # 每组内按 total_score 排序
    result: list[ScoredStock] = []
    for sector, stocks in sorted(groups.items(), key=lambda x: -max(s.total_score for s in x[1])):
        ranked = sorted(stocks, key=lambda s: s.total_score, reverse=True)
        result.extend(ranked[:top_per_sector])
        if len(result) >= max_total:
            break

    # 如果还有空位，从未分板块的股票中补充
    if len(result) < max_total:
        no_sector_sorted = sorted(no_sector, key=lambda s: s.total_score, reverse=True)
        result.extend(no_sector_sorted[:max_total - len(result)])

    return result[:max_total]


def _find_weight(signal_name: str, ic_weights: dict[str, float]) -> float:
    """从 IC 权重字典中找到匹配的权重。"""
    # Exact match
    if signal_name in ic_weights:
        return ic_weights[signal_name]

    # Look up scorer via prefix mapping, then find weight by scorer key
    for prefix, scorer in _SIGNAL_TO_SCORER.items():
        if signal_name.startswith(prefix):
            return ic_weights.get(scorer, 1.0)

    return 1.0
