"""GTJA Alpha #41.

Formula: (RANK(MAX(DELTA(VWAP,3),5))*-1)
Source: 国泰君安 191 alpha 研报 (2014), alpha 41."""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import (
    delta,
    rank,
    safe_div,
    ts_max,
)

__alpha_meta__ = {
    "id": "gtja191_041",
    "theme": ["microstructure"],
    "formula_latex": "(RANK(MAX(DELTA(VWAP,3),5))*-1)",
    "columns_required": ["volume", "amount"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 9,
    "notes": "5d max of 3d delta(vwap), ranked, negated.",
}


def compute(panel: dict) -> pd.DataFrame:
    v = panel["volume"]
    vw = safe_div(panel["amount"], v * 100.0 + 1.0)
    return -1.0 * rank(ts_max(delta(vw, 3), 5))
