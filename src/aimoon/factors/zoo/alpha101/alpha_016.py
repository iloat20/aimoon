"""Kakushadze Alpha #16.

Formula (paper appendix): -1 * rank(covariance(rank(high), rank(volume), 5))
Source: Kakushadze (2015), "101 Formulaic Alphas", arXiv:1601.00991, eq. 16.
"""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import (
    rank,
    ts_cov,
)

ALPHA_ID = "alpha101_016"

__alpha_meta__ = {
    "id": "alpha101_016",
    "nickname": "Kakushadze Alpha #16",
    "theme": ["volume"],
    "formula_latex": "-1 * rank(covariance(rank(high), rank(volume), 5))",
    "columns_required": ["high", "volume", "close"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_us"],
    "frequency": ["1D"],
    "decay_horizon": 5,
    "min_warmup_bars": 5,
    "notes": "",
}


def compute(panel: dict) -> pd.DataFrame:
    """Compute the alpha on the OHLCV+ panel and return a wide DataFrame."""
    high = panel["high"]
    volume = panel["volume"]

    # Helper aliases (local closures keep the file standalone & purity-safe).
    out = -1.0 * rank(ts_cov(rank(high), rank(volume), 5))
    return out
