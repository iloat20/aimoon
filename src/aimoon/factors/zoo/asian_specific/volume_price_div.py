"""A 股量价背离因子。

量价背离是 A 股技术分析中的经典信号：
- 价涨量缩：上涨乏力，可能见顶
- 价跌量缩：下跌减缓，可能见底

本因子计算价格变化与成交量变化的滚动相关系数，
负相关系数表示量价背离。
"""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import delta, rank, ts_corr

__alpha_meta__ = {
    "id": "asian_volume_price_divergence",
    "nickname": "量价背离",
    "theme": ["volume", "reversal", "A股特有"],
    "formula_latex": (
        r"-1 \times \mathrm{ts\_corr}("
        r"\mathrm{rank}(\Delta \mathrm{close}), "
        r"\mathrm{rank}(\Delta \mathrm{volume}), 10)"
    ),
    "columns_required": ["close", "volume"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 15,
    "notes": (
        "A 股量价背离因子。价格变化排名与成交量变化排名的 10 日滚动相关系数。"
        "取负值：负相关 = 量价背离（价涨量缩或价跌量缩），是反转信号。"
        "注意: 反转因子，高分 = 量价背离程度强。"
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """计算量价背离因子。

    Returns
    -------
    pd.DataFrame
        量价背离因子时间序列。负值 = 量价背离。
    """
    close = panel["close"]
    volume = panel["volume"]

    # 价格变化和成交量变化
    price_change = delta(close, 1)
    volume_change = delta(volume, 1)

    # 截面排名后计算 10 日滚动相关系数
    price_rank = rank(price_change)
    volume_rank = rank(volume_change)

    # 取负值: 负相关 = 量价背离
    return -1.0 * ts_corr(price_rank, volume_rank, 10)
