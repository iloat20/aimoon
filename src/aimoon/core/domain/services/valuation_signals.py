"""Domain service: 股债性价比(equity-bond attractiveness)信号灯。

纯函数,零 LLM token,零 IO。把原散落在 report 表现层的业务逻辑收敛到领域层,
使 presentation 只依赖 core。常量 ``CGB_10Y`` 也在此作为单一事实源,供 AI 工具复用,
消除跨层的重复定义。
"""

from __future__ import annotations

from aimoon.core.domain.entities.financial import FinancialData
from aimoon.core.domain.entities.quote import StockQuote

# 10 年期国债收益率锚(用于股债相对价值比较)
CGB_10Y = 0.025


def build_equity_bond_signal(
    q: StockQuote,
    financial: FinancialData,
    history_financial: list[FinancialData] | None = None,
) -> dict[str, object]:
    """股债性价比信号灯:股息率 vs 10Y 国债收益率 + FCF 覆盖 + 历史分位 + 信号。

    全部来自确定性数据,零 LLM token。口径与 fcf_dividend 工具一致
    (FCF 覆盖 = (经营现金流 − capex) / 分红现金),确保与报告「自由现金流与股息」表自洽。
    """
    mc = float(getattr(q, "market_cap", 0) or 0)
    div = float(getattr(financial, "dividend_paid", 0) or 0)
    ocf = float(getattr(financial, "operating_cf", 0) or 0)
    capex = float(getattr(financial, "capex", 0) or 0)

    dy = (div / mc) if (mc > 0 and div > 0) else None
    yield_vs_cgb = (dy - CGB_10Y) if dy is not None else None
    fcf_cover = ((ocf - capex) / div) if div > 0 else None

    # 当前股息率在「归一化历史序列」中的分位(以当前市值作分母代理估值水平)
    percentile: float | None = None
    series: list[float] = []
    if dy is not None and mc > 0 and history_financial:
        series = [
            float(getattr(fd, "dividend_paid", 0) or 0) / mc
            for fd in history_financial
            if float(getattr(fd, "dividend_paid", 0) or 0) > 0
        ]
        series.append(dy)
        if len(series) >= 2:
            below = sum(1 for x in series if x <= dy)
            percentile = below / len(series) * 100

    if yield_vs_cgb is None:
        signal = "N/A"
    elif yield_vs_cgb >= 0.04:
        signal = "🟢 极度低估（债券强替代）"
    elif yield_vs_cgb >= 0.015:
        signal = "🟡 估值偏低"
    else:
        signal = "🔴 股贵于债"

    return {
        "dividend_yield": dy,
        "cgb_10y": CGB_10Y,
        "yield_vs_cgb": yield_vs_cgb,
        "fcf_cover": fcf_cover,
        "percentile": percentile,
        "sample_years": len(series) if percentile is not None else None,
        "signal": signal,
    }
