"""GTJA Alpha 106 (国泰君安 191 短周期交易型 alpha 因子, 2014).

Formula (verbatim from the report):
    CLOSE - DELAY(CLOSE,20)

Notes:
"""

from __future__ import annotations

from aimoon.factors.base import (
    delta,
)

ALPHA_ID = "gtja191_106"

__alpha_meta__ = {
    "id": "gtja191_106",
    "theme": ["momentum"],
    "formula_latex": "close-delay(close,20)",
    "columns_required": ["close"],
    "extras_required": [],
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 20,
    "min_warmup_bars": 21,
    "notes": "",
}


def compute(panel):
    """Compute gtja191_106.

    Args:
        panel: dict[str, pd.DataFrame] with at least the required columns.

    Returns:
        pd.DataFrame with index = panel["close"].index, columns = panel["close"].columns.
    """
    c = panel["close"]
    out = delta(c, 20)
    return out
