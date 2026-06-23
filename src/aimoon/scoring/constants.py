"""Scoring module — constants, weights, and threshold definitions."""

# Dimension weights (must sum to 1.0)
WEIGHT_SENTIMENT = 0.25      # 市场情绪
WEIGHT_TECHNICAL = 0.15      # 技术面
WEIGHT_FUNDAMENTAL = 0.30    # 基本面
WEIGHT_CAPITAL_FLOW = 0.15   # 资金面
WEIGHT_NEWS = 0.15           # 新闻舆情

# Sentiment scoring thresholds (bull_ratio = positive / total)
SENTIMENT_THRESHOLDS = [
    (0.7, 5),    # >= 70% → 5 分
    (0.6, 4),    # >= 60% → 4 分
    (0.4, 3),    # >= 40% → 3 分
    (0.2, 2),    # >= 20% → 2 分
]

# Fundamental scoring thresholds
FUND_ROE_EXCELLENT = 15      # ROE >= 15% → +1
FUND_ROE_POOR = 8            # ROE < 8% → -1 (if > 0)
FUND_REVENUE_GOOD = 10       # 营收同比 >= 10% → +1
FUND_REVENUE_BAD = -5        # 营收同比 < -5% → -1
FUND_PROFIT_GOOD = 10        # 净利润同比 >= 10% → +1
FUND_PROFIT_BAD = -10        # 净利润同比 < -10% → -1

# News scoring thresholds
NEWS_BUY_RATIO_BULLISH = 0.6  # 买入研报 >= 60% → 4 分
NEWS_BUY_RATIO_BEARISH = 0.2  # 买入研报 <= 20% → 2 分

# Capital flow scoring thresholds (yuan)
CAPITAL_FLOW_STRONG_IN = 5e8   # > 5亿 → 强流入
CAPITAL_FLOW_IN = 1e8          # > 1亿 → 流入
CAPITAL_FLOW_OUT = -1e8        # > -1亿 → 中性
CAPITAL_FLOW_STRONG_OUT = -5e8 # < -5亿 → 强流出

# Default scores
DEFAULT_SCORE = 3
MIN_SCORE = 1
MAX_SCORE = 5
