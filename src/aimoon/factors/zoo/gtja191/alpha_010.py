"""GTJA Alpha #10.

Formula: RANK(MAX(((RET<0)?STD(RET,20):CLOSE)^2,5))
Source: 国泰君安 191 alpha 研报 (2014), alpha 10."""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import (
    rank,
    safe_div,
    ts_max,
    ts_std,
)

__alpha_meta__ = {
    "id": "gtja191_010",
    "theme": ["volatility", "reversal"],
    "formula_latex": "RANK(MAX(((RET<0)?STD(RET,20):CLOSE)^2,5))",
    "columns_required": ["close"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 21,
    "notes": "Per-day return = pct_change(1) via (close - delay(close,1))/delay(close,1).",
}


def compute(panel: dict) -> pd.DataFrame:
    c = panel["close"]
    pc = c.shift(1)
    ret = safe_div(c - pc, pc)
    s20 = ts_std(ret, 20)
    pick = ret.copy()
    pick = pick.where(ret < 0, c)
    pick = pick.where(~(ret < 0), s20)
    return rank(ts_max(pick * pick, 5))
