"""GTJA Alpha #94.

Formula: SUM(CLOSE>DELAY(CLOSE,1)?VOLUME:(CLOSE<DELAY(CLOSE,1)?-VOLUME:0),30)
Source: 国泰君安 191 alpha 研报 (2014), alpha 94."""

from __future__ import annotations

import pandas as pd

__alpha_meta__ = {
    "id": "gtja191_094",
    "theme": ["volume", "momentum"],
    "formula_latex": "SUM(CLOSE>DELAY(CLOSE,1)?VOLUME:(CLOSE<DELAY(CLOSE,1)?-VOLUME:0),30)",
    "columns_required": ["close", "volume"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 30,
    "min_warmup_bars": 31,
    "notes": "30d signed volume.",
}


def compute(panel: dict) -> pd.DataFrame:
    c = panel["close"]
    v = panel["volume"]
    pc = c.shift(1)
    signed = v.where(c > pc, -v.where(c < pc, 0.0))
    return signed.rolling(30, min_periods=30).sum()
