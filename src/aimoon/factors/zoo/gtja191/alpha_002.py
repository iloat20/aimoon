"""GTJA Alpha #2.

Formula: (-1 * DELTA(((CLOSE - LOW) - (HIGH - CLOSE)) / (HIGH - LOW), 1))
Source: 国泰君安 191 alpha 研报 (2014), alpha 2."""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import (
    delta,
    safe_div,
)

__alpha_meta__ = {
    "id": "gtja191_002",
    "theme": ["reversal", "microstructure"],
    "formula_latex": "(-1 * DELTA(((CLOSE - LOW) - (HIGH - CLOSE)) / (HIGH - LOW), 1))",
    "columns_required": ["close", "high", "low"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 1,
    "min_warmup_bars": 2,
    "notes": "Daily change in close-position-within-range.",
}


def compute(panel: dict) -> pd.DataFrame:
    c = panel["close"]
    h = panel["high"]
    l = panel["low"]
    raw = safe_div((c - l) - (h - c), h - l)
    return -1.0 * delta(raw, 1)
