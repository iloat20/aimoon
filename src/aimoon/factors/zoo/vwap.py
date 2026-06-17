"""VWAP 因子 — 成交量加权平均价格相对强弱"""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import safe_div

__alpha_meta__ = {
    "id": "vwap_20",
    "nickname": "VWAP (20d)",
    "theme": ["volume", "trend"],
    "columns_required": ["close", "high", "low", "volume"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 20,
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Compute price relative to VWAP (20-day).

    VWAP = sum(typical_price * volume) / sum(volume)
    Returns percentage deviation from VWAP as a single-value-per-stock factor.
    """
    c = panel["close"]
    h = panel["high"]
    l = panel["low"]
    v = panel["volume"]

    typical = (h + l + c) / 3.0
    cum_tp_vol = (typical * v).rolling(window=20).sum()
    cum_vol = v.rolling(window=20).sum()
    vwap = safe_div(cum_tp_vol, cum_vol)

    return safe_div(c - vwap, vwap) * 100.0
