"""GTJA Alpha #79.

Formula: SMA(MAX(CLOSE-DELAY(CLOSE,1),0),12,1)/SMA(ABS(CLOSE-DELAY(CLOSE,1)),12,1)*100
Source: 国泰君安 191 alpha 研报 (2014), alpha 79."""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import (
    safe_div,
)

__alpha_meta__ = {
    "id": "gtja191_079",
    "theme": ["momentum"],
    "formula_latex": "SMA(MAX(CLOSE-DELAY(CLOSE,1),0),12,1)/SMA(ABS(CLOSE-DELAY(CLOSE,1)),12,1)*100",
    "columns_required": ["close"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 12,
    "min_warmup_bars": 13,
    "notes": "RSI-12.",
}


def compute(panel: dict) -> pd.DataFrame:
    c = panel["close"]
    diff = c - c.shift(1)
    u = diff.clip(lower=0).ewm(alpha=1.0 / 12.0, adjust=False).mean()
    a = diff.abs().ewm(alpha=1.0 / 12.0, adjust=False).mean()
    return safe_div(u, a) * 100.0
