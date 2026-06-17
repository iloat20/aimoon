"""A 股涨停强度因子。

涨停是 A 股独特现象，反映市场情绪和资金追逐强度。
涨停定义为 close == high（收盘价等于最高价）。
过去 N 日涨停频率越高，说明该股票受到市场追捧。

注意：A 股涨跌停幅度因股票类型而异：
- 主板/中小板: ±10%
- 创业板/科创板: ±20%
- ST 股票: ±5%
- 北交所: ±30%

本因子简化处理，仅用 close == high 判断涨停。
"""

from __future__ import annotations

import pandas as pd

__alpha_meta__ = {
    "id": "asian_limit_up_intensity",
    "nickname": "涨停强度",
    "theme": ["sentiment", "momentum", "A股特有"],
    "formula_latex": r"\frac{1}{N}\sum_{i=1}^{N} \mathbb{1}[\mathrm{close}_{t-i} = \mathrm{high}_{t-i}]",
    "columns_required": ["close", "high"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 20,
    "notes": (
        "A 股涨停强度因子。过去 5 日内涨停次数 / 5。"
        "涨停定义为 close == high（简化判断）。"
        "高分 = 频繁涨停，市场情绪高涨。"
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """计算 5 日涨停强度。

    Returns
    -------
    pd.DataFrame
        涨停强度时间序列，值域 [0, 1]。
    """
    close = panel["close"]
    high = panel["high"]

    # 涨停判断: close == high
    limit_up = (close == high).astype(float)

    # 5 日涨停强度
    return limit_up.rolling(window=5, min_periods=5).sum() / 5.0
