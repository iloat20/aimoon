"""GTJA Alpha #99.

Formula: (-1*RANK(COVIANCE(RANK(CLOSE),RANK(VOLUME),5)))
Source: 国泰君安 191 alpha 研报 (2014), alpha 99."""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import (
    rank,
    ts_cov,
)

__alpha_meta__ = {
    "id": "gtja191_099",
    "theme": ["volume"],
    "formula_latex": "(-1*RANK(COVIANCE(RANK(CLOSE),RANK(VOLUME),5)))",
    "columns_required": ["close", "volume"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 6,
    "notes": "Negated rank of 5d cov(rank close, rank volume).",
}


def compute(panel: dict) -> pd.DataFrame:
    return -1.0 * rank(ts_cov(rank(panel["close"]), rank(panel["volume"]), 5))
