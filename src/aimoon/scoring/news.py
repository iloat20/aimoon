"""News sentiment scoring based on research reports."""

from __future__ import annotations

from ..models.stock import ResearchReportData
from .constants import (
    DEFAULT_SCORE,
    NEWS_BUY_RATIO_BEARISH,
    NEWS_BUY_RATIO_BULLISH,
)


def news_score(research: ResearchReportData) -> tuple[int, str]:
    """Rule-based 1-5 news sentiment score from research reports.

    Returns (score 1-5, detail_text).
    """
    score = DEFAULT_SCORE
    detail = "详见报告正文（新闻分析）。"

    if not research or research.total_count <= 0:
        return score, detail

    buy_ratio = (
        (research.buy_count + research.hold_count) / research.total_count
        if research.total_count > 0
        else 0
    )

    if buy_ratio >= 0.8:
        score = 5
    elif buy_ratio >= NEWS_BUY_RATIO_BULLISH:
        score = 4
    elif buy_ratio <= 0:
        score = 1
    elif buy_ratio <= NEWS_BUY_RATIO_BEARISH:
        score = 2

    detail = (
        f"机构研报{research.total_count}份，"
        f"买入{research.buy_count}份，增持{research.hold_count}份。"
    )

    return score, detail
