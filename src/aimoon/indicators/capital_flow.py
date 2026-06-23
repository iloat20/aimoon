"""Capital-flow scoring — moved from collectors/fund_flow.py to indicators."""

from __future__ import annotations

from ..models.stock import CapitalFlowData
from ..scoring.constants import (
    CAPITAL_FLOW_IN,
    CAPITAL_FLOW_OUT,
    CAPITAL_FLOW_STRONG_IN,
    CAPITAL_FLOW_STRONG_OUT,
    DEFAULT_SCORE,
    MAX_SCORE,
    MIN_SCORE,
)


def capital_flow_score(cf: CapitalFlowData) -> tuple[int, str, str]:
    """Rule-based 1-5 capital-flow score.

    Returns (score 1-5, detail_text, main_force_label).
    Label is one of "流入"/"流出"/"持平".
    """
    main_5d = cf.main_net_5d

    if main_5d > 0:
        main_force = "流入"
    elif main_5d < 0:
        main_force = "流出"
    else:
        main_force = "持平"

    if main_5d > CAPITAL_FLOW_STRONG_IN:
        s1 = 5
    elif main_5d > CAPITAL_FLOW_IN:
        s1 = 4
    elif main_5d > CAPITAL_FLOW_OUT:
        s1 = 3
    elif main_5d > CAPITAL_FLOW_STRONG_OUT:
        s1 = 2
    else:
        s1 = 1

    # Trend: 3d vs 10d direction consistency
    trend_score = 0
    if cf.net_3d > 0 and cf.net_10d > 0:
        trend_score = 2
    elif cf.net_3d < 0 and cf.net_10d < 0:
        trend_score = -1

    # 20d long-term trend
    long_score = 0
    if cf.net_20d > 5e8:
        long_score = 2
    elif cf.net_20d > 0:
        long_score = 1
    elif cf.net_20d < -5e8:
        long_score = -2
    elif cf.net_20d < 0:
        long_score = -1

    if cf.northbound_chg != 0:
        s4 = (
            5
            if cf.northbound_chg > 1e8
            else (
                4 if cf.northbound_chg > 0 else (2 if cf.northbound_chg > -1e8 else 1)
            )
        )
    else:
        s4 = 3

    if cf.lhb_date and cf.lhb_net_buy > 0:
        s5 = 5
    elif cf.lhb_date and cf.lhb_net_buy < 0:
        s5 = 2
    else:
        s5 = 3

    total = s1 * 0.35 + trend_score + long_score + s4 * 0.15 + s5 * 0.05
    total = max(MIN_SCORE, min(MAX_SCORE, DEFAULT_SCORE + total))
    score = max(MIN_SCORE, min(MAX_SCORE, round(total)))

    parts = [
        f"近5日主力净流入{main_5d / 1e8:.2f}亿",
        f"3日{cf.net_3d / 1e8:+.2f}亿",
        f"10日{cf.net_10d / 1e8:+.2f}亿",
        f"20日{cf.net_20d / 1e8:+.2f}亿",
    ]
    if cf.northbound_chg:
        nb = cf.northbound_chg / 1e8
        parts.append(f"北向变化{nb:+.2f}亿")
    if cf.lhb_date:
        parts.append(f"龙虎榜({cf.lhb_date})净买{cf.lhb_net_buy / 1e8:.2f}亿")

    detail = "；".join(parts) + "。"
    return score, detail, main_force
