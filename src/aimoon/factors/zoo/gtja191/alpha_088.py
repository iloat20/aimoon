"""GTJA Alpha #88.

Formula: (CLOSE-DELAY(CLOSE,20))/DELAY(CLOSE,20)*100
Source: 国泰君安 191 alpha 研报 (2014), alpha 88."""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import (
    safe_div,
)

__alpha_meta__ = {
    "id": "gtja191_088",
    "theme": ["momentum"],
    "formula_latex": "(CLOSE-DELAY(CLOSE,20))/DELAY(CLOSE,20)*100",
    "columns_required": ["close"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 20,
    "min_warmup_bars": 21,
    "notes": "20d return pct.",
}


def compute(panel: dict) -> pd.DataFrame:
    c = panel["close"]
    pc = c.shift(20)
    return safe_div(c - pc, pc) * 100.0
