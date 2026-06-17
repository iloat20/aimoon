"""academic MKT_RF: market factor — 21 日收益截面标准化。

Reference:
    Sharpe, W. F. (1964). "Capital Asset Prices: A Theory of Market Equilibrium
    under Conditions of Risk." The Journal of Finance, 19(3), 425-442.

原始 CAPM 市场因子是市场组合的超额收益。
这里用 21 日个股收益的截面 z-score 作为市场因子暴露的代理——
近期涨幅大的股票有更高的市场 beta 暴露。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from aimoon.factors.base import delta, safe_div

__alpha_meta__ = {
    "id": "academic_mkt_rf",
    "nickname": "[PRICE PROXY] Market factor (Sharpe 1964) — 21d demeaned return",
    "theme": ["momentum"],
    "formula_latex": (
        r"\mathrm{zscore}_{x}\bigl((\mathrm{close}_t - "
        r"\mathrm{close}_{t-21}) / \mathrm{close}_{t-21}\bigr)"
    ),
    "columns_required": ["close"],
    "universe": ["equity_us", "equity_cn", "equity_hk"],
    "frequency": ["1d"],
    "decay_horizon": 21,
    "min_warmup_bars": 21,
    "notes": (
        "[PRICE PROXY] for the Sharpe (1964) / Fama-French market factor "
        "(MKT-RF). The original definition uses value-weighted market excess "
        "returns; here we use a 21-day per-stock total return and cross-sectional "
        "z-score per date for long-short ranking. Top z-scores = strong recent "
        "winners; bottom = losers."
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
    """Return 21-day return cross-sectional z-score per stock."""
    close = panel["close"]
    ret = safe_div(delta(close, 21), close.shift(21))
    return _cross_sectional_zscore(ret)
