"""评分服务 — 基本面、资金面、舆情评分的规则化评估。"""

from __future__ import annotations

from aimoon.core.domain.entities import (
    CapitalFlowData,
    FinancialData,
    ResearchReportData,
)
from aimoon.core.domain.value_objects.analysis_report import AnalysisReport

# 维度权重
WEIGHT_FUNDAMENTAL = 0.50
WEIGHT_CAPITAL_FLOW = 0.25
WEIGHT_NEWS = 0.25

# 基本面评分阈值
FUND_ROE_EXCELLENT = 15
FUND_ROE_POOR = 8
FUND_REVENUE_GOOD = 10
FUND_REVENUE_BAD = -5
FUND_PROFIT_GOOD = 10
FUND_PROFIT_BAD = -10

# 舆情评分阈值
NEWS_BUY_RATIO_BULLISH = 0.6
NEWS_BUY_RATIO_BEARISH = 0.2

# 资金流评分阈值（元）
CAPITAL_FLOW_STRONG_IN = 5e8
CAPITAL_FLOW_IN = 1e8
CAPITAL_FLOW_OUT = -1e8
NORTHBOUND_THRESHOLD = 1e8

# 资金面各维度权重
CF_W_5D = 0.35
CF_W_TREND = 0.25
CF_W_20D = 0.20
CF_W_NORTH = 0.15
CF_W_LHB = 0.05

# 默认分值
DEFAULT_SCORE = 3
MIN_SCORE = 1
MAX_SCORE = 5


def fundamental_score(financial: FinancialData) -> tuple[int, str]:
    """基于财务数据的 1-5 分基本面评分。

    返回 (score 1-5, detail_text)。
    """
    score = DEFAULT_SCORE
    detail = "详见报告正文（基本面分析）。"

    if not financial or not financial.report_period:
        return score, detail

    parts: list[str] = []

    if financial.roe > FUND_ROE_EXCELLENT:
        score += 1
        parts.append(f"ROE {financial.roe}%优秀")
    elif financial.roe > FUND_ROE_POOR:
        parts.append(f"ROE {financial.roe}%良好")
    elif financial.roe > 0:
        score -= 1
        parts.append(f"ROE {financial.roe}%偏低")
    elif financial.roe == 0:
        score -= 1
        parts.append("ROE 0%盈亏平衡")
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
        parts.append(f"净利润同比+{financial.net_profit_yoy:.1f}%")
    elif financial.net_profit_yoy < FUND_PROFIT_BAD:
        score -= 1
        parts.append(f"净利润同比{financial.net_profit_yoy:.1f}%")

    score = max(MIN_SCORE, min(MAX_SCORE, score))
    detail = "；".join(parts) if parts else "详见报告正文。"

    return score, detail


def _score_range(value: float, thresholds: list[tuple[float, int]]) -> int:
    """根据阈值列表返回对应评分。

    thresholds 按降序排列，每项为 (下限, 评分)。
    第一个满足 value > 下限 的项决定返回值。
    若无匹配则返回最后一项的评分。

    示例::

        _score_range(6e8, [(5e8, 5), (1e8, 4), (0, 3), (-1e8, 2)])  # → 5
    """
    for boundary, score in thresholds:
        if value > boundary:
            return score
    return thresholds[-1][1]


def capital_flow_score(cf: CapitalFlowData) -> tuple[int, str, str]:
    """基于资金流向数据的 1-5 分评分。

    返回 (score 1-5, detail_text, main_force_label)。
    Label 为 "流入"/"流出"/"持平" 之一。
    """
    main_5d = cf.main_net_5d

    is_neutral = (
        main_5d == 0
        and cf.main_net_3d == 0
        and cf.main_net_10d == 0
        and cf.main_net_20d == 0
        and cf.northbound_chg == 0
        and (not cf.lhb_date or cf.lhb_net_buy == 0)
    )
    if is_neutral:
        detail = "近5日主力净流入0.00亿；3日+0.00亿；10日+0.00亿；20日+0.00亿。"
        return 3, detail, "持平"

    net_all = main_5d + cf.main_net_3d + cf.main_net_10d + cf.main_net_20d
    if net_all > 0:
        main_force = "流入"
    elif net_all < 0:
        main_force = "流出"
    else:
        main_force = "持平"

    s1 = _score_range(
        main_5d,
        [
            (CAPITAL_FLOW_STRONG_IN, 5),
            (CAPITAL_FLOW_IN, 4),
            (0, 3),
            (CAPITAL_FLOW_OUT, 2),
        ],
    )

    trend_score = 0
    if cf.main_net_3d > 0 and cf.main_net_10d > 0:
        trend_score = 2
    elif cf.main_net_3d < 0 and cf.main_net_10d < 0:
        trend_score = -2

    long_score = 0
    if cf.main_net_20d > CAPITAL_FLOW_STRONG_IN:
        long_score = 2
    elif cf.main_net_20d > 0:
        long_score = 1
    elif cf.main_net_20d < -CAPITAL_FLOW_STRONG_IN:
        long_score = -2
    elif cf.main_net_20d < 0:
        long_score = -1

    if cf.northbound_chg == 0:
        s4 = 3
    elif cf.northbound_chg > NORTHBOUND_THRESHOLD:
        s4 = 5
    elif cf.northbound_chg > 0:
        s4 = 4
    elif cf.northbound_chg > -NORTHBOUND_THRESHOLD:
        s4 = 2
    else:
        s4 = 1

    if cf.lhb_date and cf.lhb_net_buy > 0:
        s5 = 5
    elif cf.lhb_date and cf.lhb_net_buy < 0:
        s5 = 2
    else:
        s5 = 3

    score = DEFAULT_SCORE + (
        (s1 - 3) * CF_W_5D
        + trend_score * CF_W_TREND
        + long_score * CF_W_20D
        + (s4 - 3) * CF_W_NORTH
        + (s5 - 3) * CF_W_LHB
    )
    score = max(MIN_SCORE, min(MAX_SCORE, round(score)))

    parts = [
        f"近5日主力净流入{main_5d / 1e8:.2f}亿",
        f"3日{cf.main_net_3d / 1e8:+.2f}亿",
        f"10日{cf.main_net_10d / 1e8:+.2f}亿",
        f"20日{cf.main_net_20d / 1e8:+.2f}亿",
    ]
    if cf.northbound_chg:
        nb = cf.northbound_chg / 1e8
        parts.append(f"北向变化{nb:+.2f}亿")
    if cf.lhb_date:
        lhb_net = cf.lhb_net_buy or 0.0
        parts.append(f"龙虎榜({cf.lhb_date})净买{lhb_net / 1e8:.2f}亿")

    detail = "；".join(parts) + "。"
    return score, detail, main_force


def news_score(research: ResearchReportData) -> tuple[int, str]:
    """基于机构研报的 1-5 分舆情评分。

    返回 (score 1-5, detail_text)。
    """
    score = DEFAULT_SCORE
    detail = "详见报告正文（新闻分析）。"

    if not research or research.total_count <= 0:
        return score, detail

    # bullish_ratio: 买入+增持占比（含推荐/增持等偏多信号）
    bullish_ratio = (research.buy_count + research.hold_count) / research.total_count

    if bullish_ratio >= 0.8:
        score = 5
    elif bullish_ratio >= NEWS_BUY_RATIO_BULLISH:
        score = 4
    elif bullish_ratio <= 0.05:
        score = 1
    elif bullish_ratio <= NEWS_BUY_RATIO_BEARISH:
        score = 2

    detail = (
        f"机构研报{research.total_count}份，"
        f"买入{research.buy_count}份，增持{research.hold_count}份。"
    )

    return score, detail


def calculate_total_score(report: AnalysisReport) -> float:
    """基于三个维度的加权总分。

    使用 WEIGHT_FUNDAMENTAL、WEIGHT_CAPITAL_FLOW、WEIGHT_NEWS 权重
    对基本面、资金面、舆情评分进行加权计算。

    返回加权总分（范围 1-5）。
    """
    total = (
        report.fundamental.score * WEIGHT_FUNDAMENTAL
        + report.capital_flow.score * WEIGHT_CAPITAL_FLOW
        + report.news.score * WEIGHT_NEWS
    )
    return round(total, 2)
