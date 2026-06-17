"""academic SMB: size factor — 对数美元成交量代理。

Reference:
    Fama, E. F., & French, K. R. (1993). "Common risk factors in the returns on
    stocks and bonds." Journal of Financial Economics, 33(1), 3-56.

原始 SMB (Small Minus Big) 按市值排序。
这里用 60 日对数美元成交量的负值作为代理——
小市值股票通常有较低的美元成交量。
负值排名：高分 = 小市值，低分 = 大市值。
注意：这是规模因子的流动性代理，最好用市值数据。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from aimoon.factors.base import ts_mean

__alpha_meta__ = {
    "id": "academic_smb",
    "nickname": "[PRICE PROXY] FF1993 SMB — small-minus-big via inverse dollar-volume",
    "theme": ["quality"],
    "formula_latex": (
        r"\mathrm{zscore}_{x}\bigl(-\log(\mathrm{ts\_mean}(\mathrm{volume}"
        r" \cdot \mathrm{close},\,60) + 1)\bigr)"
    ),
    "columns_required": ["close", "volume"],
    "universe": ["equity_us", "equity_cn", "equity_hk"],
    "frequency": ["1d"],
    "decay_horizon": 60,
    "min_warmup_bars": 60,
    "notes": (
        "[PRICE PROXY] for the Fama-French (1993) SMB (Small Minus Big) size "
        "factor. The original definition uses market capitalization from book "
        "equity data; here we use the negative log of 60-day average dollar "
        "volume (close * volume) as a liquidity-weighted size proxy, then "
        "cross-sectional z-score per date for long-short ranking. "
        "Top z-scores = smaller / less liquid names."
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
    """Return inverse log 60-day dollar-volume z-score per stock."""
    close = panel["close"]
    volume = panel["volume"]
    dollar_volume = volume * close
    avg = ts_mean(dollar_volume, 60)
    log_size = np.log(avg + 1.0)
    return _cross_sectional_zscore(-log_size)
