"""GTJA Alpha #70.

Formula: STD(AMOUNT,6)
Source: 国泰君安 191 alpha 研报 (2014), alpha 70."""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import (
    ts_std,
)

__alpha_meta__ = {
    "id": "gtja191_070",
    "theme": ["volatility", "volume"],
    "formula_latex": "STD(AMOUNT,6)",
    "columns_required": ["amount"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 6,
    "min_warmup_bars": 7,
    "notes": "6d std of amount (turnover).",
}


def compute(panel: dict) -> pd.DataFrame:
    return ts_std(panel["amount"], 6)
