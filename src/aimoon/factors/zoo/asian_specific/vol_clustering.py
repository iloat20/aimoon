"""A 股波动率聚集因子。

A 股存在波动率聚集现象：高波动后往往继续高波动。
同时，波动率急剧上升通常伴随市场恐慌，是短期反转信号。

波动率聚集 = ts_std(returns, 5) / ts_std(returns, 20)
比值 > 1 表示短期波动率高于长期波动率（波动率上升）。
取负值：波动率上升是恐慌信号，短期可能反转。
"""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import delta, safe_div, ts_std

__alpha_meta__ = {
    "id": "asian_volatility_clustering",
    "nickname": "波动率聚集",
    "theme": ["volatility", "reversal", "A股特有"],
    "formula_latex": (r"-1 \times \frac{\mathrm{ts\_std}(r, 5)}{\mathrm{ts\_std}(r, 20)}"),
    "columns_required": ["close"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 25,
    "notes": (
        "A 股波动率聚集因子。5 日波动率 / 20 日波动率的负值。"
        "比值 > 1 表示短期波动率上升（恐慌），取负值后 < -1。"
        "高分 = 波动率稳定或下降，市场平静。"
        "低分 = 波动率急剧上升，可能反转。"
        "注意: 反转因子，高分看多。"
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """计算波动率聚集因子。

    Returns
    -------
    pd.DataFrame
        波动率聚集因子时间序列。负值 = 波动率上升。
    """
    close = panel["close"]
    returns = safe_div(delta(close, 1), close.shift(1))

    # 短期波动率 / 长期波动率
    vol_short = ts_std(returns, 5)
    vol_long = ts_std(returns, 20)

    # 取负值: 波动率上升是恐慌信号
    return -1.0 * safe_div(vol_short, vol_long)
