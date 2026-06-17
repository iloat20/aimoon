"""GTJA Alpha #62.

Formula: ((-1*CORR(HIGH,RANK(VOLUME),5)))
Source: 国泰君安 191 alpha 研报 (2014), alpha 62."""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import (
    rank,
    ts_corr,
)

__alpha_meta__ = {
    "id": "gtja191_062",
    "theme": ["volume"],
    "formula_latex": "((-1*CORR(HIGH,RANK(VOLUME),5)))",
    "columns_required": ["high", "volume"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 6,
    "notes": "Negated 5d corr(high, rank(volume)).",
}


def compute(panel: dict) -> pd.DataFrame:
    return -1.0 * ts_corr(panel["high"], rank(panel["volume"]), 5)
