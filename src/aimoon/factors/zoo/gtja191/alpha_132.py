"""GTJA Alpha 132 (国泰君安 191 短周期交易型 alpha 因子, 2014).

Formula (verbatim from the report):
    MEAN(AMOUNT,20)

Notes:
"""

from __future__ import annotations

from aimoon.factors.base import (
    ts_mean,
)

ALPHA_ID = "gtja191_132"

__alpha_meta__ = {
    "id": "gtja191_132",
    "theme": ["liquidity"],
    "formula_latex": "mean(amount,20)",
    "columns_required": ["close", "amount"],
    "extras_required": [],
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 20,
    "min_warmup_bars": 20,
    "notes": "",
}


def compute(panel):
    """Compute gtja191_132.

    Args:
        panel: dict[str, pd.DataFrame] with at least the required columns.

    Returns:
        pd.DataFrame with index = panel["close"].index, columns = panel["close"].columns.
    """
    amt = panel["amount"]
    out = ts_mean(amt, 20)
    return out
