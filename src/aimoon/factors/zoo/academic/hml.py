"""academic HML: value factor — 长期反转代理。

Reference:
    Fama, E. F., & French, K. R. (1993). "Common risk factors in the returns on
    stocks and bonds." Journal of Financial Economics, 33(1), 3-56.

原始 HML (High Minus Low) 按账面市值比排序。
这里用 252 日负收益作为代理——长期表现不佳的股票更可能被低估
（价值股），长期表现优异的股票更可能被高估（成长股）。
注意：这是价值因子的弱代理，最好用 PE/PB 等基本面数据。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from aimoon.factors.base import delta, safe_div

__alpha_meta__ = {
    "id": "academic_hml",
    "nickname": "[PRICE PROXY] FF1993 HML — value via inverse 252d return",
    "theme": ["value"],
    "formula_latex": (
        r"\mathrm{zscore}_{x}\bigl(-(\mathrm{close}_t - "
        r"\mathrm{close}_{t-252}) / \mathrm{close}_{t-252}\bigr)"
    ),
    "columns_required": ["close"],
    "universe": ["equity_us", "equity_cn", "equity_hk"],
    "frequency": ["1d"],
    "decay_horizon": 60,
    "min_warmup_bars": 252,
    "notes": (
        "[PRICE PROXY] for the Fama-French (1993) HML (High Minus Low) value "
        "factor. The original definition uses book-to-market ratio from "
        "fundamental data; here we use the negative 252-day total return as a "
        "long-term reversal proxy, then cross-sectional z-score per date for "
        "long-short ranking. Top z-scores = long-term underperformers (deeper "
        "value). Canonical 252d window; declared decay_horizon=60 due to "
        "registry schema cap (le=60); real signal horizon=252."
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
    """Return inverse 252-day return cross-sectional z-score per stock.

    Uses the canonical 252-day window without silent shrink on short panels.
    Short panels produce an all-NaN result, which the registry surfaces as a
    >95% NaN error so the user sees "insufficient history" instead of a
    misleading shrunk-window value.
    """
    close = panel["close"]
    ret = safe_div(delta(close, 252), close.shift(252))
    return _cross_sectional_zscore(-ret)
