"""GTJA Alpha #65.

Formula: MEAN(CLOSE,6)/CLOSE
Source: 国泰君安 191 alpha 研报 (2014), alpha 65."""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import (
    safe_div,
    ts_mean,
)

__alpha_meta__ = {
    "id": "gtja191_065",
    "theme": ["reversal"],
    "formula_latex": "MEAN(CLOSE,6)/CLOSE",
    "columns_required": ["close"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 6,
    "min_warmup_bars": 7,
    "notes": "MA6 over close.",
}


def compute(panel: dict) -> pd.DataFrame:
    c = panel["close"]
    return safe_div(ts_mean(c, 6), c)
