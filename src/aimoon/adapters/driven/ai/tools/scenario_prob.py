"""情景概率加权与风险收益比工具(纯函数,零 LLM token)。

输入:
- valuation: valuation 工具输出(含 fcfe_targets: conservative/neutral/optimistic 的 price/pe)
- quote: StockQuote(含 price 现价,用于计算上行/下行空间)
- fin_temporal: financial_temporal 输出(增长/OCF 质量/ROE 趋势,用于赋概率)

输出:
- 三档情景的概率权重(基于增长动能、盈利质量、估值分位动态分配,归一化到 100%)
- 加权期望目标价 / 期望 PE
- 中性情景下行空间、乐观情景上行空间、风险收益比(非对称量化)
- prob_basis: 概率赋值依据(一句话,便于报告引用)

概率逻辑(透明、可解释,非黑箱):
- 增长动能:revenue_cagr 越高 → 乐观权重越高
- 盈利质量:ocf_profit_ratio(OCF/净利润)越高 → 乐观权重越高
- 盈利趋势:roe_trend 末项相对峰值跌幅越大 → 保守权重越高
- 估值分位:当前 PE 超过近三年均值上轨越多 → 保守权重越高
"""
from __future__ import annotations

import logging

from aimoon.adapters.driven.ai.tools._safe import tool_safe
from aimoon.core.domain.entities.quote import StockQuote

from ._common import _hist_pe_anchor

logger = logging.getLogger(__name__)


@tool_safe("computation_error")
def run(
    valuation: dict | None,
    quote: StockQuote | None,
    fin_temporal: dict | None,
) -> dict[str, object]:
    if valuation is None:
        return {"__partial__": "missing_valuation"}
    if quote is None:
        return {"__partial__": "missing_quote"}

    targets = valuation.get("fcfe_targets") or {}
    if not isinstance(targets, dict) or not targets:
        return {"__partial__": "missing_targets"}

    price = float(quote.price or 0.0)
    if price <= 0:
        return {"__partial__": "missing_price"}

    def _tier_price(tier: str) -> float | None:
        t = targets.get(tier)
        if isinstance(t, dict):
            v = t.get("price")
            return float(v) if v is not None else None
        if isinstance(t, (int, float)):
            return float(t)
        return None

    cons_p = _tier_price("conservative")
    neut_p = _tier_price("neutral")
    opt_p = _tier_price("optimistic")
    if neut_p is None or opt_p is None:
        # 至少要有中性/乐观两档才能算风险收益比;缺失则标 partial
        return {"__partial__": "incomplete_targets", "targets": _weighted_targets(targets, None)}

    probs = _assign_probs(fin_temporal, quote, cons_p, neut_p, opt_p)

    # 加权期望目标价
    expected = 0.0
    for tier, p in probs.items():
        tp = _tier_price(tier)
        if tp is not None:
            expected += tp * p
    expected = round(expected, 2)

    # 风险收益比(非对称)
    downside = (neut_p - price) / price if neut_p else 0.0  # 中性情景相对现价的下行
    upside = (opt_p - price) / price if opt_p else 0.0        # 乐观情景相对现价的上行
    rr = None
    if downside < 0 and upside > 0:
        rr = round(abs(upside) / abs(downside), 2)

    # 期望 PE
    def _tier_pe(tier: str) -> float | None:
        t = targets.get(tier)
        if isinstance(t, dict):
            pe = t.get("pe")
            return float(pe) if pe is not None else None
        return None

    exp_pe = 0.0
    for tier, p in probs.items():
        pe = _tier_pe(tier)
        if pe is not None:
            exp_pe += pe * p
    exp_pe = round(exp_pe, 2) if exp_pe else 0.0

    weighted_targets = {
        tier: {
            "price": _tier_price(tier),
            "pe": _tier_pe(tier),
            "probability": round(probs.get(tier, 0.0) * 100, 1),
        }
        for tier in ("conservative", "neutral", "optimistic")
    }

    return {
        "targets": weighted_targets,
        "expected_target": expected,
        "expected_pe": exp_pe,
        "downside_neutral_pct": round(downside * 100, 1),
        "upside_optimistic_pct": round(upside * 100, 1),
        "risk_reward_ratio": rr,
        "current_price": round(price, 2),
        "prob_basis": _prob_basis(fin_temporal, quote),
    }


def _assign_probs(
    fin_temporal: dict | None,
    quote: StockQuote,
    cons_p: float | None,
    neut_p: float,
    opt_p: float,
) -> dict[str, float]:
    """返回 {conservative, neutral, optimistic}: 未归一化的权重(0-1)。"""
    rev_cagr = float(fin_temporal.get("revenue_cagr") or 0.0) if fin_temporal else 0.0
    ocf_ratio = float(fin_temporal.get("ocf_profit_ratio") or 0.0) if fin_temporal else 0.0
    roe_trend = (fin_temporal.get("roe_trend") or []) if fin_temporal else []
    pe = float(quote.pe or 0.0)

    # 乐观驱动分:增长 + 盈利质量
    growth_score = max(0.0, min(1.0, (rev_cagr + 0.05) / 0.20))  # cagr -5%~15% → 0~1
    quality_score = max(0.0, min(1.0, ocf_ratio / 1.0))          # OCF/净利 0~1
    opt_w = 0.25 + 0.45 * (0.6 * growth_score + 0.4 * quality_score)

    # 保守驱动分:ROE 压缩 + 估值偏高
    cons_w = 0.20
    if len(roe_trend) >= 2:
        peak = max(roe_trend)
        latest = roe_trend[0]
        drop = max(0.0, peak - latest)
        cons_w += min(0.25, drop * 3.0)
    # 历史 PE 分位:依赖逐年 PE 序列(未采集,恒为 0),该分支按设计不触发。
    hist_anchor = _hist_pe_anchor(fin_temporal)
    if hist_anchor > 0 and pe > hist_anchor * 1.15:
        cons_w += min(0.20, (pe / hist_anchor - 1.0) * 0.6)

    cons_w = max(0.05, min(0.6, cons_w))
    opt_w = max(0.05, min(0.6, opt_w))
    # 中性 = 剩余
    neut_w = max(0.1, 1.0 - cons_w - opt_w)

    total = cons_w + neut_w + opt_w
    return {
        "conservative": cons_w / total,
        "neutral": neut_w / total,
        "optimistic": opt_w / total,
    }


def _prob_basis(fin_temporal: dict | None, quote: StockQuote) -> str:
    rev_cagr = float(fin_temporal.get("revenue_cagr") or 0.0) if fin_temporal else 0.0
    ocf_ratio = float(fin_temporal.get("ocf_profit_ratio") or 0.0) if fin_temporal else 0.0
    pe = float(quote.pe or 0.0)
    return (
        f"基于营收CAGR {rev_cagr * 100:.1f}%、OCF/净利 {ocf_ratio:.2f}、"
        f"当前PE {pe:.1f} 动态赋权(增长动能与盈利质量推升乐观权重,估值偏高推升保守权重)"
    )


def _weighted_targets(targets: dict, probs: dict | None) -> dict:
    """降级路径:无概率时直接透传 price/pe。"""
    out = {}
    for tier in ("conservative", "neutral", "optimistic"):
        t = targets.get(tier)
        if isinstance(t, dict):
            out[tier] = {
                "price": t.get("price"),
                "pe": t.get("pe"),
                "probability": None,
            }
        elif isinstance(t, (int, float)):
            out[tier] = {"price": t, "pe": None, "probability": None}
    return out
