"""GTJA Alpha 103 (国泰君安 191 短周期交易型 alpha 因子, 2014).

Formula (verbatim from the report):
    ((20-LOWDAY(low,20))/20)*100

Notes: LOWDAY -> ts_argmin (0-based); (20 - argmin)/20 * 100.
"""

from __future__ import annotations

from aimoon.factors.base import (
    ts_argmin,
)

ALPHA_ID = "gtja191_103"

__alpha_meta__ = {
    "id": "gtja191_103",
    "theme": ["reversal"],
    "formula_latex": "((20-lowday(low,20))/20)*100",
    "columns_required": ["close", "low"],
    "extras_required": [],
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 20,
    "min_warmup_bars": 20,
    "notes": "LOWDAY -> ts_argmin (0-based); (20 - argmin)/20 * 100.",
}


def compute(panel):
    """Compute gtja191_103.

    Args:
        panel: dict[str, pd.DataFrame] with at least the required columns.

    Returns:
        pd.DataFrame with index = panel["close"].index, columns = panel["close"].columns.
    """
    l = panel["low"]
    am = ts_argmin(l, 20)
    out = (20.0 - am) / 20.0 * 100.0
    return out
