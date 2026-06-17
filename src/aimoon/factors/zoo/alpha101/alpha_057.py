"""Kakushadze Alpha #57.

Formula (paper appendix): 0 - 1 * ((close-vwap) / decay_linear(rank(ts_argmax(close,30)), 2))
Source: Kakushadze (2015), "101 Formulaic Alphas", arXiv:1601.00991, eq. 57.
"""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import (
    decay_linear,
    rank,
    safe_div,
    ts_argmax,
)

ALPHA_ID = "alpha101_057"

__alpha_meta__ = {
    "id": "alpha101_057",
    "nickname": "Kakushadze Alpha #57",
    "theme": ["reversal"],
    "formula_latex": "0 - 1 * ((close-vwap) / decay_linear(rank(ts_argmax(close,30)), 2))",
    "columns_required": ["close", "vwap"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_us"],
    "frequency": ["1D"],
    "decay_horizon": 5,
    "min_warmup_bars": 32,
    "notes": "",
}


def compute(panel: dict) -> pd.DataFrame:
    """Compute the alpha on the OHLCV+ panel and return a wide DataFrame."""
    close = panel["close"]
    vwap = panel["vwap"]

    # Helper aliases (local closures keep the file standalone & purity-safe).
    out = 0.0 - safe_div((close - vwap), decay_linear(rank(ts_argmax(close, 30)), 2))
    return out
