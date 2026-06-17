"""A 股日内动量因子。

A 股 T+1 制度下，日内动量（开盘到收盘的价格变化）是重要的信号。
如果股票经常在日内从低点到高点（close > open），说明日内多头力量强。

日内动量 = (close - open) / open 的 N 日滚动均值。
"""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import safe_div, ts_mean

__alpha_meta__ = {
    "id": "asian_intraday_momentum",
    "nickname": "日内动量",
    "theme": ["momentum", "A股特有"],
    "formula_latex": (
        r"\mathrm{ts\_mean}\left("
        r"\frac{\mathrm{close} - \mathrm{open}}{\mathrm{open}}, 10\right)"
    ),
    "columns_required": ["close", "open"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 15,
    "notes": (
        "A 股日内动量因子。(close - open) / open 的 10 日滚动均值。"
        "高分 = 日内经常从低到高，多头力量强。"
        "低分 = 日内经常从高到低，空头力量强。"
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """计算 10 日日内动量因子。

    Returns
    -------
    pd.DataFrame
        日内动量时间序列。
    """
    close = panel["close"]
    open_ = panel["open"]

    # 日内收益率
    intraday_ret = safe_div(close - open_, open_)

    # 10 日滚动均值
    return ts_mean(intraday_ret, 10)
