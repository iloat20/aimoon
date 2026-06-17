"""GTJA Alpha 153 (国泰君安 191 短周期交易型 alpha 因子, 2014).

Formula (verbatim from the report):
    (MEAN(CLOSE,3)+MEAN(CLOSE,6)+MEAN(CLOSE,12)+MEAN(CLOSE,24))/4

Notes:
"""

from __future__ import annotations

from aimoon.factors.base import (
    ts_mean,
)

ALPHA_ID = "gtja191_153"

__alpha_meta__ = {
    "id": "gtja191_153",
    "theme": ["momentum"],
    "formula_latex": "(mean(c,3)+mean(c,6)+mean(c,12)+mean(c,24))/4",
    "columns_required": ["close"],
    "extras_required": [],
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 24,
    "min_warmup_bars": 24,
    "notes": "",
}


def compute(panel):
    """Compute gtja191_153.

    Args:
        panel: dict[str, pd.DataFrame] with at least the required columns.

    Returns:
        pd.DataFrame with index = panel["close"].index, columns = panel["close"].columns.
    """
    c = panel["close"]
    out = (ts_mean(c, 3) + ts_mean(c, 6) + ts_mean(c, 12) + ts_mean(c, 24)) / 4.0
    return out
