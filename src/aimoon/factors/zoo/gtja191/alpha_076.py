"""GTJA Alpha #76.

Formula: STD(ABS((CLOSE/DELAY(CLOSE,1)-1))/VOLUME,20)/MEAN(ABS((CLOSE/DELAY(CLOSE,1)-1))/VOLUME,20)
Source: 国泰君安 191 alpha 研报 (2014), alpha 76."""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import (
    safe_div,
    ts_mean,
    ts_std,
)

__alpha_meta__ = {
    "id": "gtja191_076",
    "theme": ["volatility", "volume"],
    "formula_latex": "STD(ABS((CLOSE/DELAY(CLOSE,1)-1))/VOLUME,20)/MEAN(ABS((CLOSE/DELAY(CLOSE,1)-1))/VOLUME,20)",
    "columns_required": ["close", "volume"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 20,
    "min_warmup_bars": 22,
    "notes": "Coefficient-of-variation of |daily return|/volume over 20 days.",
}


def compute(panel: dict) -> pd.DataFrame:
    c = panel["close"]
    v = panel["volume"]
    x = safe_div((safe_div(c, c.shift(1)) - 1.0).abs(), v)
    return safe_div(ts_std(x, 20), ts_mean(x, 20))
