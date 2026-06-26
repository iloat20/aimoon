"""Scoring package — rule-based scoring for all analysis dimensions."""

from .capital_flow import capital_flow_score
from .constants import (
    CAPITAL_FLOW_IN,
    CAPITAL_FLOW_OUT,
    CAPITAL_FLOW_STRONG_IN,
    CAPITAL_FLOW_STRONG_OUT,
    DEFAULT_SCORE,
    FUND_PROFIT_BAD,
    FUND_PROFIT_GOOD,
    FUND_REVENUE_BAD,
    FUND_REVENUE_GOOD,
    FUND_ROE_EXCELLENT,
    FUND_ROE_POOR,
    MAX_SCORE,
    MIN_SCORE,
    NEWS_BUY_RATIO_BEARISH,
    NEWS_BUY_RATIO_BULLISH,
    WEIGHT_CAPITAL_FLOW,
    WEIGHT_FUNDAMENTAL,
    WEIGHT_NEWS,
)
from .fundamental import fundamental_score
from .news import news_score

__all__ = [
    # Scoring functions
    "capital_flow_score",
    "fundamental_score",
    "news_score",
    # Weights
    "WEIGHT_FUNDAMENTAL",
    "WEIGHT_CAPITAL_FLOW",
    "WEIGHT_NEWS",
    # Thresholds
    "FUND_ROE_EXCELLENT",
    "FUND_ROE_POOR",
    "FUND_REVENUE_GOOD",
    "FUND_REVENUE_BAD",
    "FUND_PROFIT_GOOD",
    "FUND_PROFIT_BAD",
    "NEWS_BUY_RATIO_BULLISH",
    "NEWS_BUY_RATIO_BEARISH",
    "CAPITAL_FLOW_STRONG_IN",
    "CAPITAL_FLOW_IN",
    "CAPITAL_FLOW_OUT",
    "CAPITAL_FLOW_STRONG_OUT",
    "DEFAULT_SCORE",
    "MIN_SCORE",
    "MAX_SCORE",
]
