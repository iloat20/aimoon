"""Kakushadze Alpha #25.

Formula (paper appendix): rank((((-1*returns)*adv20)*vwap)*(high-close))
Source: Kakushadze (2015), "101 Formulaic Alphas", arXiv:1601.00991, eq. 25.
"""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import (
    rank,
    ts_mean,
)

ALPHA_ID = "alpha101_025"

__alpha_meta__ = {
    "id": "alpha101_025",
    "nickname": "Kakushadze Alpha #25",
    "theme": ["momentum", "volume"],
    "formula_latex": "rank((((-1*returns)*adv20)*vwap)*(high-close))",
    "columns_required": ["high", "close", "volume", "vwap"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_us"],
    "frequency": ["1D"],
    "decay_horizon": 5,
    "min_warmup_bars": 21,
    "notes": "",
}


def compute(panel: dict) -> pd.DataFrame:
    """Compute the alpha on the OHLCV+ panel and return a wide DataFrame."""
    close = panel["close"]
    high = panel["high"]
    volume = panel["volume"]
    vwap = panel["vwap"]
    adv20 = ts_mean(volume, 20)
    returns = close.pct_change(fill_method=None)
    # Helper aliases (local closures keep the file standalone & purity-safe).
    out = rank(((-1.0 * returns) * adv20) * vwap * (high - close))
    return out
