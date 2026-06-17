"""GTJA Alpha #14.

Formula: CLOSE - DELAY(CLOSE,5)
Source: 国泰君安 191 alpha 研报 (2014), alpha 14."""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import (
    delta,
)

__alpha_meta__ = {
    "id": "gtja191_014",
    "theme": ["momentum"],
    "formula_latex": "CLOSE - DELAY(CLOSE,5)",
    "columns_required": ["close"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 6,
    "notes": "Simple 5d momentum = delta(close, 5).",
}


def compute(panel: dict) -> pd.DataFrame:
    return delta(panel["close"], 5)
