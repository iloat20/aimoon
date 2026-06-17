"""GTJA Alpha 120 (国泰君安 191 短周期交易型 alpha 因子, 2014).

Formula (verbatim from the report):
    (RANK((VWAP - CLOSE)) / RANK((VWAP + CLOSE)))

Notes:
"""

from __future__ import annotations

from aimoon.factors.base import (
    rank,
    safe_div,
    vwap,
)

ALPHA_ID = "gtja191_120"

__alpha_meta__ = {
    "id": "gtja191_120",
    "theme": ["reversal"],
    "formula_latex": "rank(vwap-close)/rank(vwap+close)",
    "columns_required": ["open", "high", "low", "close", "volume", "amount"],
    "extras_required": [],
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 1,
    "min_warmup_bars": 1,
    "notes": "",
}


def compute(panel):
    """Compute gtja191_120.

    Args:
        panel: dict[str, pd.DataFrame] with at least the required columns.

    Returns:
        pd.DataFrame with index = panel["close"].index, columns = panel["close"].columns.
    """
    c = panel["close"]
    vw = vwap(panel, "equity_cn")

    out = safe_div(rank(vw - c), rank(vw + c))
    return out
