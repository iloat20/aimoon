"""academic CMA: investment factor — 价格增长代理。

Reference:
    Fama, E. F., & French, K. R. (2015). "A five-factor asset pricing model."
    Journal of Financial Economics, 116(1), 1-22.

原始 CMA (Conservative Minus Aggressive) 按总资产增长率排序。
这里用 60 日价格增长率的负值作为代理——价格快速增长的公司
通常处于扩张阶段（激进），价格稳定/收缩的公司更保守。
负值排名：高分 = 保守（低增长），低分 = 激进（高增长）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from aimoon.factors.base import delta, safe_div

__alpha_meta__ = {
    "id": "academic_cma",
    "nickname": "[PRICE PROXY] FF2015 CMA — investment via 60d price growth",
    "theme": ["quality"],
    "formula_latex": (
        r"\mathrm{zscore}_{x}\bigl(-(\mathrm{close}_t - "
        r"\mathrm{close}_{t-60}) / \mathrm{close}_{t-60}\bigr)"
    ),
    "columns_required": ["close"],
    "universe": ["equity_us", "equity_cn", "equity_hk"],
    "frequency": ["1d"],
    "decay_horizon": 60,
    "min_warmup_bars": 120,
    "notes": (
        "[PRICE PROXY] for the Fama-French (2015) CMA (Conservative Minus "
        "Aggressive) investment factor. The original definition uses total-asset "
        "growth from fundamental data; here we use the negative 60-day price "
        "change as a growth proxy, then cross-sectional z-score per date for "
        "long-short ranking. Top z-scores = low growth (conservative proxy)."
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
    """Return inverse 60-day price change cross-sectional z-score per stock.

    Uses the canonical 60-bar window without silent shrink on short panels.
    Short panels produce an all-NaN result, which the registry surfaces as a
    >95% NaN error so the user sees "insufficient history" rather than a
    misleading shrunk-window value.
    """
    close = panel["close"]
    growth = safe_div(delta(close, 60), close.shift(60))
    return _cross_sectional_zscore(-growth)
