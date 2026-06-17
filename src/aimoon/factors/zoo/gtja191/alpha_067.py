"""GTJA Alpha #67.

Formula: SMA(MAX(CLOSE-DELAY(CLOSE,1),0),24,1)/SMA(ABS(CLOSE-DELAY(CLOSE,1)),24,1)*100
Source: 国泰君安 191 alpha 研报 (2014), alpha 67."""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import (
    safe_div,
)

__alpha_meta__ = {
    "id": "gtja191_067",
    "theme": ["momentum"],
    "formula_latex": "SMA(MAX(CLOSE-DELAY(CLOSE,1),0),24,1)/SMA(ABS(CLOSE-DELAY(CLOSE,1)),24,1)*100",
    "columns_required": ["close"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 24,
    "min_warmup_bars": 25,
    "notes": "RSI-24 style.",
}


def compute(panel: dict) -> pd.DataFrame:
    c = panel["close"]
    diff = c - c.shift(1)
    up_part = diff.clip(lower=0)
    abs_part = diff.abs()
    u = up_part.ewm(alpha=1.0 / 24.0, adjust=False).mean()
    a = abs_part.ewm(alpha=1.0 / 24.0, adjust=False).mean()
    return safe_div(u, a) * 100.0
