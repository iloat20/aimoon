"""GTJA Alpha #97.

Formula: STD(VOLUME,10)
Source: 国泰君安 191 alpha 研报 (2014), alpha 97."""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import (
    ts_std,
)

__alpha_meta__ = {
    "id": "gtja191_097",
    "theme": ["volatility", "volume"],
    "formula_latex": "STD(VOLUME,10)",
    "columns_required": ["volume"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 11,
    "notes": "10d std of volume.",
}


def compute(panel: dict) -> pd.DataFrame:
    return ts_std(panel["volume"], 10)
