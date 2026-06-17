"""A 股跌停逃离因子。

跌停时收盘价等于最低价，但如果后续价格回升（low < close），
说明有资金在跌停板买入，是短期反转信号。

跌停逃离 = (close - low) / (high - low)
该值在跌停日接近 0，但如果 close > low 则 > 0，说明有资金抄底。
取 5 日反转均值作为因子值。
"""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import safe_div, ts_mean

__alpha_meta__ = {
    "id": "asian_limit_down_escape",
    "nickname": "跌停逃离",
    "theme": ["reversal", "sentiment", "A股特有"],
    "formula_latex": (
        r"\mathrm{ts\_mean}\left("
        r"\frac{\mathrm{close} - \mathrm{low}}{\mathrm{high} - \mathrm{low}}, 5\right)"
    ),
    "columns_required": ["close", "high", "low"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 10,
    "notes": (
        "A 股跌停逃离因子。(close - low) / (high - low) 的 5 日均值。"
        "在跌停日该值接近 0，有资金抄底时该值 > 0。"
        "高分 = 近期有跌停但资金在抄底，短期反转信号。"
        "注意: 反转因子，高分看多。"
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """计算 5 日跌停逃离因子。

    Returns
    -------
    pd.DataFrame
        跌停逃离因子时间序列，值域 [0, 1]。
    """
    close = panel["close"]
    high = panel["high"]
    low = panel["low"]

    # 日内价格位置: 0 = 跌停, 1 = 涨停
    position = safe_div(close - low, high - low)

    # 5 日均值
    return ts_mean(position, 5)
