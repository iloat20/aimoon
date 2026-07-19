"""自由现金流与股息工具(纯函数,零 LLM token)。

输入:
- fin_temporal: financial_temporal 工具输出(含 operating_cf / investing_cf 年度序列)
- financial: FinancialData 实体(含 statements 三大表明细、net_profit)
- quote: StockQuote(含 price / market_cap,用于推导股本与股息率)

输出:
- fcf / ocf / capex 及 FCF 利润率
- 股息支付率、股息率(对比 10Y 国债收益率)、FCF-分红 缺口 —— 用于判断分红可持续性
所有缺失项显式标 N/A,绝不编造。
"""
from __future__ import annotations

import logging

from aimoon.adapters.driven.ai.tools._safe import tool_safe
from aimoon.core.domain.entities.financial import FinancialData
from aimoon.core.domain.entities.quote import StockQuote
from aimoon.core.domain.services.valuation_signals import CGB_10Y

from ._common import (
    _capex,
    _first_year_investing,
    _first_year_ocf,
)

logger = logging.getLogger(__name__)


@tool_safe("computation_error")
def run(
    fin_temporal: dict | None,
    financial: FinancialData | None,
    quote: StockQuote | None,
) -> dict[str, object]:
    if fin_temporal is None:
        return {"__partial__": "missing_fin_temporal"}
    if quote is None:
        return {"__partial__": "missing_quote"}

    ocf = _first_year_ocf(fin_temporal)
    if not ocf:
        return {
            "__partial__": "missing_ocf",
            "fcf": None,
            "ocf": None,
            "capex": None,
            "fcf_margin": None,
            "payout_ratio": None,
            "dividend_yield": None,
            "fcf_minus_dividend": None,
            "yield_vs_cgb": None,
        }

    investing = _first_year_investing(fin_temporal)
    real_capex = float(getattr(financial, "capex", 0.0) or 0.0) if financial else 0.0
    capex = _capex(ocf, investing, real_capex)
    fcf = ocf - capex
    np_ = float(financial.net_profit) if financial else 0.0
    fcf_margin = (fcf / np_) if np_ else None

    # 分红现金:优先从现金流量表"分配股利、利润或偿付利息支付的现金"科目读取
    div_cash = _dividend_from_statements(financial)
    payout_ratio = (div_cash / np_) if (div_cash and np_) else None
    # 股息率 = 分红总额 / 总市值
    dividend_yield = (
        div_cash / float(quote.market_cap)
        if (div_cash and quote.market_cap)
        else None
    )
    fcf_minus_dividend = (fcf - div_cash) if div_cash is not None else None
    yield_vs_cgb = (dividend_yield - CGB_10Y) if dividend_yield is not None else None

    sustainable = None
    if div_cash is not None and fcf is not None:
        # FCF 覆盖分红的倍数:<1 表示分红靠筹资/存量支撑,可持续性存疑
        sustainable = (fcf / div_cash) if div_cash else None

    return {
        "ocf": round(ocf, 4),
        "capex": round(capex, 4),
        "fcf": round(fcf, 4),
        "fcf_margin": round(fcf_margin, 4) if fcf_margin is not None else None,
        "payout_ratio": round(payout_ratio, 4) if payout_ratio is not None else None,
        "dividend_yield": round(dividend_yield, 4) if dividend_yield is not None else None,
        "dividend_total": round(div_cash, 4) if div_cash is not None else None,
        "fcf_minus_dividend": (
            round(fcf_minus_dividend, 4) if fcf_minus_dividend is not None else None
        ),
        "yield_vs_cgb": round(yield_vs_cgb, 4) if yield_vs_cgb is not None else None,
        "fcf_cover": round(sustainable, 2) if sustainable is not None else None,
        "cgb_10y": CGB_10Y,
    }


def _dividend_from_statements(financial: FinancialData | None) -> float | None:
    """从 FinancialData 读取分红现金(分配股利、利润或偿付利息支付的现金)。

    ``FinancialData`` 由 AkshareFinancialAdapter 直接填充 ``dividend_paid``
    (来自现金流量表 DIVIDEND_INTEREST_PAID / DIVIDEND_PAID 科目),模型本身
    无 ``statements`` 字段,故直接读该字段,避免整段返回 N/A。找不到则返回 None。
    """
    if financial is None:
        return None
    div = financial.dividend_paid
    if not div:
        return None
    return float(div)
