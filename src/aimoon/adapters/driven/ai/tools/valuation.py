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
    financial: object | None = None,
) -> dict[str, object]:
    try:
        if fin_temporal is None:
            return {"__partial__": "missing_fin_temporal"}
        if quote is None:
            return {"__partial__": "missing_quote"}

        pe = float(quote.pe) if (quote.pe and quote.pe > 0) else 0.0
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

        real_capex = float(getattr(financial, "capex", 0.0) or 0.0) if financial else 0.0
        capex = _capex(ocf, invest_cf, real_capex)
        base_fcfe = ocf - capex
        growth = float(fin_temporal.get("revenue_cagr") or 0.0)
        discount_rate = _discount_rate(quote)

        market_cap = float(quote.market_cap or 0.0)
        if market_cap <= 0:
            logger.info("[valuation] market_cap 缺失,无法折算每股,FCFE 标 partial")
            return {
                "__partial__": "missing_market_cap",
                "pe": pe,
                "pb": pb,
                "fcfe_targets": {},
                "fcfe_assumptions": {},
                "peer_comparison": _peer_table(peer_comp, pe, pb),
            }

        # FCFE 折现:base_fcfe>0 用标准 DCF;<=0(DCF 退化)回退分红折现(DDM)。
        if base_fcfe > 0:
            method = "fcfe_dcf"
            fcfe_targets, terminal_growth = _project_fcfe(base_fcfe, growth, discount_rate, quote)
            fcfe_assumptions = {
                "method": method,
                "growth": round(growth, 4),
                "discount_rate": round(discount_rate, 4),
                "terminal_growth": round(terminal_growth, 4),
                "years": FCFE_YEARS,
                "capex": round(capex, 4),
                "ocf": round(ocf, 4),
            }
        else:
            # capex 代理(投资现金流净流出)常含理财等非 PP&E 支出,使 FCFE 假阴性;
            # 此时 DCF 无意义,改用语折现(Gordon)回退,依赖分红可持续性。
            method = "ddm_fallback"
            logger.info("[valuation] base_fcfe=%.2f<=0,FCFE DCF 退化,回退 DDM", base_fcfe)
            fcfe_targets, _ = _project_ddm(financial, quote, market_cap, pe, discount_rate)
            fcfe_assumptions = {
                "method": method,
                "discount_rate": round(discount_rate, 4),
                "ddm_growth_tiers": {"conservative": 0.0, "neutral": 0.01, "optimistic": 0.02},
                "note": "FCFE 为负(capex 代理≥OCF),DCF 退化,改用语折现(Gordon)回退",
            }

        return {
            "pe": pe,
            "pb": pb,
            "valuation_method": method,
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
        # 总价值 → 每股目标价;无流通股本信息时无法折算每股,显式置 None
        # (不应把总价值直接当作每股价返回)。
        per_share = (equity_value / shares) if shares > 0 else None
        # 防御:权益值为负(极端情景)时不输出负值目标价,置 None → 渲染为 N/A。
        if per_share is not None and per_share <= 0:
            per_share = None
        target_pe = (per_share / eps) if (per_share is not None and eps > 0) else None
        targets[name] = {
            "price": round(per_share, 2) if per_share is not None else None,
            "pe": round(target_pe, 2) if target_pe is not None else None,
            "probability": None,  # 模型不估概率,显式 None → 渲染为 N/A
        }
    return targets, terminal_growth


def _project_ddm(
    financial: object | None,
    quote: StockQuote,
    market_cap: float,
    pe: float,
    discount_rate: float,
) -> tuple[dict[str, object], float | None]:
    """FCFE≤0 时的回退估值:分红折现(Gordon)三档正目标价。

    仅当能拿到分红现金(``financial.dividend_paid``)并用「市值/股价」推导股本时有效;
    否则三档全部返回 ``None``(渲染为 N/A)。分红增速与营收 CAGR 解耦(分红具粘性),
    用温和正增速档:保守 0% / 中性 1% / 乐观 2%。

    返回 ``(targets, None)`` —— DDM 无永续增速封顶概念,终端增速由各档 g 直接决定。
    """
    price = float(quote.price or 0.0)
    if price <= 0 or market_cap <= 0:
        return _none_targets(), None
    shares = market_cap / price
    if shares <= 0:
        return _none_targets(), None
    div_total = float(getattr(financial, "dividend_paid", 0.0) or 0.0)
    if div_total <= 0:
        return _none_targets(), None

    dps = div_total / shares
    eps = price / pe if pe > 0 else 0.0
    r = max(discount_rate, 0.001)
    tiers = {"conservative": 0.0, "neutral": 0.01, "optimistic": 0.02}
    targets: dict[str, object] = {}
    for name, g in tiers.items():
        if r <= g:
            targets[name] = {"price": None, "pe": None, "probability": None}
            continue
        p = dps * (1 + g) / (r - g)
        if p <= 0:
            targets[name] = {"price": None, "pe": None, "probability": None}
            continue
        tp = p / eps if eps > 0 else None
        targets[name] = {
            "price": round(p, 2),
            "pe": round(tp, 2) if tp is not None else None,
            "probability": None,
        }
    return targets, None


def _none_targets() -> dict[str, object]:
    """三档全 None(渲染为 N/A),用于 DDM 无法计算时的兜底。"""
    return {
        name: {"price": None, "pe": None, "probability": None}
        for name in ("conservative", "neutral", "optimistic")
    }


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
