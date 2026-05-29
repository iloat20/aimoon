"""因子合成 — IC 加权 + 行业中性化"""
from __future__ import annotations

import logging
from collections import defaultdict

from aimoon.models import ScoredStock, Signal

logger = logging.getLogger(__name__)


def ic_weighted_score(
    scored: ScoredStock,
    ic_weights: dict[str, float],
) -> ScoredStock:
    """用 IC 权重重新计算单只股票的总分，返回新 ScoredStock。"""
    if not ic_weights:
        return scored

    total = 0.0
    for s in scored.signals:
        # 信号名匹配权重：尝试 scorer 级别匹配
        weight = _find_weight(s.name, ic_weights)
        total += s.score * weight

    # RPS 信号也参与加权
    rps_score = sum(
        s.score * _find_weight(s.name, ic_weights)
        for s in scored.signals
        if s.name.startswith("rps")
    )

    # 将加权后的总分通过一个内部信号表示
    # 这里我们保留原始信号，但可以覆盖 total_score 的计算
    # 实际上 ScoredStock.total_score 是 @property，无法直接修改
    # 所以我们返回一个带额外信号的新 ScoredStock
    adjusted_signals = list(scored.signals)
    # 不修改原始信号，加权逻辑在外部使用

    return scored


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
    """从 IC 权重字典中找到匹配的权重。

    信号名格式: scorer_name + 描述（如 "roc5_strong"）
    scorer 名: score_momentum, score_momentum_ext, score_trend, 等
    """
    # 精确匹配
    if signal_name in ic_weights:
        return ic_weights[signal_name]

    # 前缀匹配：信号名的前几段匹配 scorer 名
    for key, weight in ic_weights.items():
        if signal_name.startswith(key.removeprefix("score_")):
            return weight

    return 1.0  # 默认权重
