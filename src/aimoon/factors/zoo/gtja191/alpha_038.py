"""GTJA Alpha #38.

Formula: (((SUM(HIGH,20)/20)<HIGH)?(-1*DELTA(HIGH,2)):0)
Source: 国泰君安 191 alpha 研报 (2014), alpha 38."""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import (
    delta,
    ts_mean,
)

__alpha_meta__ = {
    "id": "gtja191_038",
    "theme": ["reversal"],
    "formula_latex": "(((SUM(HIGH,20)/20)<HIGH)?(-1*DELTA(HIGH,2)):0)",
    "columns_required": ["high"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 20,
    "min_warmup_bars": 21,
    "notes": "When current high > MA20(high), output -delta(high,2); else 0.",
}


def compute(panel: dict) -> pd.DataFrame:
    h = panel["high"]
    m20 = ts_mean(h, 20)
    cond = m20 < h
    return (-1.0 * delta(h, 2)).where(cond, 0.0)
