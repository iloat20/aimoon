"""估值工具(纯函数)。

输入 financial_temporal 输出 + quote + peer_compare,输出 quote 派生 PE/PB、FCFE 三档目标价
(conservative/neutral/optimistic)及显式假设、同业横向对比。
"""
from __future__ import annotations

import logging

from aimoon.core.domain.entities.quote import StockQuote

logger = logging.getLogger(__name__)

INDUSTRIAL_CAPEX_OCF_RATIO = 0.30  # 工业类 capex 兜底:OCF 的 30%
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
        market_cap = float(quote.market_cap or 0.0)

        invest_cf = float(_first_year_ocf_investing(fin_temporal) or 0.0)
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
        fcfe_assumptions = {
            "growth": round(growth, 4),
            "discount_rate": round(discount_rate, 4),
            "years": FCFE_YEARS,
            "capex": round(capex, 4),
            "ocf": round(ocf, 4),
        }

        fcfe_targets = _project_fcfe(base_fcfe, growth, discount_rate, market_cap)

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


def _first_year_ocf(fin: dict) -> float:
    years = fin.get("years") or []
    if years and isinstance(years[0], dict):
        v = years[0].get("operating_cf")
        return float(v) if v is not None else 0.0
    return 0.0


def _first_year_ocf_investing(fin: dict) -> float:
    years = fin.get("years") or []
    if years and isinstance(years[0], dict):
        v = years[0].get("investing_cf")
        return float(v) if v is not None else 0.0
    return 0.0


def _capex(ocf: float, investing_cf: float) -> float:
    """cape 代理:投资现金流绝对值;否则工业兜底 OCF * 30%。"""
    if investing_cf < 0:
        return -investing_cf
    if ocf > 0:
        return ocf * INDUSTRIAL_CAPEX_OCF_RATIO
    return 0.0


def _discount_rate(quote: StockQuote) -> float:
    return 0.10  # 无风险 + 股权溢价综合:8%-10% 取中轨 10%


def _project_fcfe(
    base_fcfe: float,
    growth: float,
    discount_rate: float,
    market_cap: float,
) -> dict[str, object]:
    """三档情景 FCFE 折现 → 保守(低增长) / 中性 / 乐观(高增长)。"""
    growth_low = min(growth, 0.0)
    growth_high = growth + 0.05
    scenarios = {
        "conservative": {"growth": growth_low, "discount": discount_rate + 0.02},
        "neutral": {"growth": growth, "discount": discount_rate},
        "optimistic": {"growth": growth_high, "discount": discount_rate},
    }
    targets: dict[str, object] = {}
    for name, cfg in scenarios.items():
        targets[name] = _pv_fcfe(
            base_fcfe, float(cfg["growth"]), float(cfg["discount"]), market_cap
        )
    return targets


def _pv_fcfe(base_fcfe: float, growth: float, discount: float, market_cap: float) -> float:
    """对 base_fcfe 做 5 年高增长折现 + 终值,用 market_cap 兜底避免 0。"""
    r = max(discount, 0.001)
    g = growth
    if r <= g:
        g = r - 0.001  # 防止分母为负
    pv = 0.0
    fcfe = base_fcfe
    for _ in range(FCFE_YEARS):
        pv += fcfe / (1 + r) ** (_ + 1)
        fcfe *= 1 + g
    terminal = fcfe / (r - g) / (1 + r) ** FCFE_YEARS
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
