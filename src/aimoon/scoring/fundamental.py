"""Fundamental scoring based on financial data."""

from __future__ import annotations

from ..models.stock import FinancialData
from .constants import (
    DEFAULT_SCORE,
    FUND_PROFIT_BAD,
    FUND_PROFIT_GOOD,
    FUND_REVENUE_BAD,
    FUND_REVENUE_GOOD,
    FUND_ROE_EXCELLENT,
    FUND_ROE_POOR,
)


def fundamental_score(financial: FinancialData) -> tuple[int, str]:
    """Rule-based 1-5 fundamental score from financial data.

    Returns (score 1-5, detail_text).
    """
    score = DEFAULT_SCORE
    detail = "详见报告正文（基本面分析）。"

    if not financial or not financial.report_period:
        return score, detail

    parts = []

    if financial.roe > FUND_ROE_EXCELLENT:
        score += 1
        parts.append(f"ROE {financial.roe}%优秀")
    elif financial.roe > FUND_ROE_POOR:
        parts.append(f"ROE {financial.roe}%良好")
    elif financial.roe > 0:
        score -= 1
        parts.append(f"ROE {financial.roe}%偏低")
    else:
        score -= 2
        parts.append(f"ROE {financial.roe}%亏损")

    if financial.revenue_yoy > FUND_REVENUE_GOOD:
        score += 1
        parts.append(f"营收同比+{financial.revenue_yoy:.1f}%")
    elif financial.revenue_yoy < FUND_REVENUE_BAD:
        score -= 1
        parts.append(f"营收同比{financial.revenue_yoy:.1f}%")

    if financial.net_profit_yoy > FUND_PROFIT_GOOD:
        score += 1
    elif financial.net_profit_yoy < FUND_PROFIT_BAD:
        score -= 1

    score = max(1, min(5, score))
    detail = "；".join(parts) if parts else "详见报告正文。"

    return score, detail
