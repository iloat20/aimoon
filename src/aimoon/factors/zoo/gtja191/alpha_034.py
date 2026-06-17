"""GTJA Alpha #34.

Formula: MEAN(CLOSE,12)/CLOSE
Source: 国泰君安 191 alpha 研报 (2014), alpha 34."""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import (
    safe_div,
    ts_mean,
)

__alpha_meta__ = {
    "id": "gtja191_034",
    "theme": ["reversal"],
    "formula_latex": "MEAN(CLOSE,12)/CLOSE",
    "columns_required": ["close"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 12,
    "min_warmup_bars": 13,
    "notes": "MA12 over close.",
}


def compute(panel: dict) -> pd.DataFrame:
    c = panel["close"]
    return safe_div(ts_mean(c, 12), c)
