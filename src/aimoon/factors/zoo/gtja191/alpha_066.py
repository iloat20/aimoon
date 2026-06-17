"""GTJA Alpha #66.

Formula: (CLOSE-MEAN(CLOSE,6))/MEAN(CLOSE,6)*100
Source: 国泰君安 191 alpha 研报 (2014), alpha 66."""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import (
    safe_div,
    ts_mean,
)

__alpha_meta__ = {
    "id": "gtja191_066",
    "theme": ["reversal"],
    "formula_latex": "(CLOSE-MEAN(CLOSE,6))/MEAN(CLOSE,6)*100",
    "columns_required": ["close"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 6,
    "min_warmup_bars": 7,
    "notes": "Bias-6 pct.",
}


def compute(panel: dict) -> pd.DataFrame:
    c = panel["close"]
    m6 = ts_mean(c, 6)
    return safe_div(c - m6, m6) * 100.0
