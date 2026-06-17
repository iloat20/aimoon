"""GTJA Alpha #89.

Formula: 2*(SMA(CLOSE,13,2)-SMA(CLOSE,27,2)-SMA(SMA(CLOSE,13,2)-SMA(CLOSE,27,2),10,2))
Source: 国泰君安 191 alpha 研报 (2014), alpha 89."""

from __future__ import annotations

import pandas as pd

__alpha_meta__ = {
    "id": "gtja191_089",
    "theme": ["momentum"],
    "formula_latex": "2*(SMA(CLOSE,13,2)-SMA(CLOSE,27,2)-SMA(SMA(CLOSE,13,2)-SMA(CLOSE,27,2),10,2))",
    "columns_required": ["close"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 27,
    "min_warmup_bars": 28,
    "notes": "MACD-like signal.",
}


def compute(panel: dict) -> pd.DataFrame:
    c = panel["close"]
    short = c.ewm(alpha=2.0 / 13.0, adjust=False).mean()
    long_ = c.ewm(alpha=2.0 / 27.0, adjust=False).mean()
    dif = short - long_
    dea = dif.ewm(alpha=2.0 / 10.0, adjust=False).mean()
    return 2.0 * (dif - dea)
