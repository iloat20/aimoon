"""GTJA Alpha 133 (国泰君安 191 短周期交易型 alpha 因子, 2014).

Formula (verbatim from the report):
    ((20-HIGHDAY(HIGH,20))/20)*100 - ((20-LOWDAY(LOW,20))/20)*100

Notes:
"""

from __future__ import annotations

from aimoon.factors.base import (
    ts_argmax,
    ts_argmin,
)

ALPHA_ID = "gtja191_133"

__alpha_meta__ = {
    "id": "gtja191_133",
    "theme": ["momentum"],
    "formula_latex": "((20-highday(high,20))/20)*100-((20-lowday(low,20))/20)*100",
    "columns_required": ["close", "high", "low"],
    "extras_required": [],
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 20,
    "min_warmup_bars": 20,
    "notes": "",
}


def compute(panel):
    """Compute gtja191_133.

    Args:
        panel: dict[str, pd.DataFrame] with at least the required columns.

    Returns:
        pd.DataFrame with index = panel["close"].index, columns = panel["close"].columns.
    """
    h = panel["high"]
    l = panel["low"]
    out = (20.0 - ts_argmax(h, 20)) / 20.0 * 100.0 - (20.0 - ts_argmin(l, 20)) / 20.0 * 100.0
    return out
