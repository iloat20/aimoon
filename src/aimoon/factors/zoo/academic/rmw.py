"""academic RMW: profitability factor — 短期夏普比率代理。

Reference:
    Fama, E. F., & French, K. R. (2015). "A five-factor asset pricing model."
    Journal of Financial Economics, 116(1), 1-22.

原始 RMW (Robust Minus Weak) 按营业利润率排序。
这里用 20 日夏普比率（日均收益 / 日波动率）作为代理——
高盈利的公司通常有更高的收益/风险比（更稳健的盈利模式）。
高分 = 高夏普 = 高质量，低分 = 低夏普 = 低质量。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from aimoon.factors.base import delta, safe_div, ts_mean, ts_std

__alpha_meta__ = {
    "id": "academic_rmw",
    "nickname": "[PRICE PROXY] FF2015 RMW — quality via 20d Sharpe ratio",
    "theme": ["quality"],
    "formula_latex": (
        r"\mathrm{zscore}_{x}\bigl(" r"\mathrm{ts\_mean}(r, 20) / \mathrm{ts\_std}(r, 20)\bigr)"
    ),
    "columns_required": ["close"],
    "universe": ["equity_us", "equity_cn", "equity_hk"],
    "frequency": ["1d"],
    "decay_horizon": 20,
    "min_warmup_bars": 40,
    "notes": (
        "[PRICE PROXY] for the Fama-French (2015) RMW (Robust Minus Weak) "
        "profitability factor. The original definition uses operating "
        "profitability from fundamental data; here we use the 20-day Sharpe "
        "ratio (mean return / std return) as a quality proxy, then "
        "cross-sectional z-score per date for long-short ranking. "
        "Top z-scores = higher Sharpe (quality / robust)."
    ),
}


def _cross_sectional_zscore(df: pd.DataFrame) -> pd.DataFrame:
    """Per-row z-score: (x - row_mean) / row_std; zero/NaN std rows -> NaN."""
    mean = df.mean(axis=1, skipna=True)
    std = df.std(axis=1, ddof=1, skipna=True)
    centered = df.sub(mean, axis=0)
    result = centered.div(std.where(std > 0), axis=0)
    return result.replace([np.inf, -np.inf], np.nan)


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return 20-day Sharpe ratio cross-sectional z-score per stock."""
    close = panel["close"]
    ret_1d = safe_div(delta(close, 1), close.shift(1))
    mean_ret = ts_mean(ret_1d, 20)
    std_ret = ts_std(ret_1d, 20)
    # 夏普比率 = 日均收益 / 日波动率
    sharpe = safe_div(mean_ret, std_ret)
    return _cross_sectional_zscore(sharpe)
