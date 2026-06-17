"""GTJA Alpha #16.

Formula: (-1 * TSMAX(RANK(CORR(RANK(VOLUME), RANK(VWAP), 5)), 5))
Source: 国泰君安 191 alpha 研报 (2014), alpha 16."""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import (
    rank,
    safe_div,
    ts_corr,
    ts_max,
)

__alpha_meta__ = {
    "id": "gtja191_016",
    "theme": ["volume", "microstructure"],
    "formula_latex": "(-1 * TSMAX(RANK(CORR(RANK(VOLUME), RANK(VWAP), 5)), 5))",
    "columns_required": ["volume", "amount"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 11,
    "notes": "Max over 5d of rank of rolling rank-volume vs rank-vwap correlation.",
}


def compute(panel: dict) -> pd.DataFrame:
    v = panel["volume"]
    vw = safe_div(panel["amount"], v * 100.0 + 1.0)
    return -1.0 * ts_max(rank(ts_corr(rank(v), rank(vw), 5)), 5)
