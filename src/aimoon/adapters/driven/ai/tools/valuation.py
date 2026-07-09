"""估值工具(纯函数)。

输入 financial_temporal 输出 + quote + peer_compare,输出 quote 派生 PE/PB、FCFE 三档目标价
(conservative/neutral/optimistic)及显式假设、同业横向对比。
"""
from __future__ import annotations

import logging

from aimoon.core.domain.entities.quote import StockQuote

from ._common import (
    _capex,
    _first_year_investing,
    _first_year_ocf,
)

logger = logging.getLogger(__name__)

FCFE_YEARS = 5


def run(
    fin_temporal: dict | None,
    quote: StockQuote | None,
    peer_comp: dict | None,
) -> dict[str, object]:
    try:
        if fin_temporal is None:
            return {"__partial__": "missing_fin_temporal"}
        if quote is None:
            return {"__partial__": "missing_quote"}

        pe = float(quote.pe or 0.0)
        pb = float(quote.pb or 0.0)

        invest_cf = float(_first_year_investing(fin_temporal) or 0.0)
        ocf_partial = bool(fin_temporal.get("ocf_partial"))
        ocf = _first_year_ocf(fin_temporal)

        fcfe_targets: dict[str, object] = {}
        fcfe_assumptions: dict[str, object] = {}
        if ocf_partial or not ocf:
            logger.info("[valuation] OCF 缺失,FCFE 标 partial")
            return {
                "__partial__": "missing_ocf",
                "pe": pe,
                "pb": pb,
                "fcfe_targets": fcfe_targets,
                "fcfe_assumptions": fcfe_assumptions,
                "peer_comparison": _peer_table(peer_comp, pe, pb),
            }

        capex = _capex(ocf, invest_cf)
        base_fcfe = ocf - capex
        growth = float(fin_temporal.get("revenue_cagr") or 0.0)
        discount_rate = _discount_rate(quote)
        fcfe_targets, terminal_growth = _project_fcfe(base_fcfe, growth, discount_rate, quote)
        fcfe_assumptions = {
            "growth": round(growth, 4),
            "discount_rate": round(discount_rate, 4),
            "terminal_growth": round(terminal_growth, 4),
            "years": FCFE_YEARS,
            "capex": round(capex, 4),
            "ocf": round(ocf, 4),
        }

        return {
            "pe": pe,
            "pb": pb,
            "fcfe_targets": fcfe_targets,
            "fcfe_assumptions": fcfe_assumptions,
            "peer_comparison": _peer_table(peer_comp, pe, pb),
        }
    except Exception as e:
        logger.debug("[valuation] partial: %s: %s", type(e).__name__, e)
        return {"__partial__": "computation_error"}


def _discount_rate(quote: StockQuote) -> float:
    return 0.10  # 无风险 + 股权溢价综合:8%-10% 取中轨 10%


def _project_fcfe(
    base_fcfe: float,
    growth: float,
    discount_rate: float,
    quote: StockQuote,
) -> tuple[dict[str, object], float]:
    """三档情景 FCFE 折现 → 保守(低增长) / 中性 / 乐观(高增长)。

    返回每股目标价(元) + 对应 PE + 概率(None,模型不估概率)。
    总价值按「流通股本 = market_cap / 价格」折算为每股,避免总价值误标为每股价。
    """
    price = float(quote.price or 0.0)
    market_cap = float(quote.market_cap or 0.0)
    pe = float(quote.pe or 0.0)
    # 流通股本:用「市值 / 股价」推导(采集器已填充 market_cap)。
    shares = market_cap / price if price > 0 else 0.0
    # EPS = 股价 / PE,用于把目标价换算为 PE 倍数。
    eps = price / pe if pe > 0 else 0.0
    # 永续增速封顶:取「中性增速 / 2」且不超过 2.5%、不低于 0,并严格低于折现率。
    terminal_growth = min(max(growth / 2, 0.0), 0.025)
    if terminal_growth >= discount_rate:
        terminal_growth = discount_rate / 2

    growth_low = min(growth, 0.0)
    growth_high = growth + 0.05
    scenarios = {
        "conservative": {"growth": growth_low, "discount": discount_rate + 0.02},
        "neutral": {"growth": growth, "discount": discount_rate},
        "optimistic": {"growth": growth_high, "discount": discount_rate},
    }
    targets: dict[str, object] = {}
    for name, cfg in scenarios.items():
        equity_value = _pv_fcfe(
            base_fcfe, float(cfg["growth"]), float(cfg["discount"]), terminal_growth, market_cap
        )
        # 总价值 → 每股目标价;无股本信息时退化为总价值(极端兜底)。
        per_share = (equity_value / shares) if shares > 0 else equity_value
        target_pe = (per_share / eps) if eps > 0 else None
        targets[name] = {
            "price": round(per_share, 2),
            "pe": round(target_pe, 2) if target_pe is not None else None,
            "probability": None,  # 模型不估概率,显式 None → 渲染为 N/A
        }
    return targets, terminal_growth


def _pv_fcfe(
    base_fcfe: float, growth: float, discount: float, terminal_growth: float, market_cap: float,
) -> float:
    """对 base_fcfe 做 5 年情景增速折现 + 永续终值(Gordon,增速封顶 < 折现率)。

    终值用 ``terminal_growth``(封顶 < discount)计算,避免高增长情景分母趋 0 爆炸;
    ``market_cap`` 仅作零值兜底。
    """
    r = max(discount, 0.001)
    g = min(growth, r - 0.001)  # 显式预测期增速也封顶,避免 (1+r)^N 前出现负分母
    pv = 0.0
    fcfe = base_fcfe
    for _ in range(FCFE_YEARS):
        pv += fcfe / (1 + r) ** (_ + 1)
        fcfe *= 1 + g
    # 永续终值:第 N 年 FCFE 按永续增速 g_t 永续增长,折现回现值。
    gt = min(terminal_growth, r * 0.5)  # 永续增速严格低于折现率(取半轨兜底)
    terminal = fcfe * (1 + gt) / (r - gt) / (1 + r) ** FCFE_YEARS
    return round(pv + terminal, 2)


def _peer_table(peer_comp: dict | None, self_pe: float, self_pb: float) -> list[dict[str, object]]:
    """横向同业对比表,包含自身锚。"""
    rows: list[dict[str, object]] = []
    if peer_comp:
        for p in peer_comp.get("peers") or []:
            rows.append(
                {
                    "name": p.get("name", ""),
                    "pe": float(p.get("pe") or 0.0),
                    "pb": float(p.get("pb") or 0.0),
                }
            )
    rows.insert(0, {"name": "(self)", "pe": self_pe, "pb": self_pb})
    return rows
