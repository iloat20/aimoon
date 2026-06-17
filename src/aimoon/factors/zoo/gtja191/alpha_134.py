"""GTJA Alpha 134 (国泰君安 191 短周期交易型 alpha 因子, 2014).

Formula (verbatim from the report):
    (CLOSE-DELAY(CLOSE,12))/DELAY(CLOSE,12)*VOLUME

Notes:
"""

from __future__ import annotations

from aimoon.factors.base import (
    safe_div,
)

ALPHA_ID = "gtja191_134"

__alpha_meta__ = {
    "id": "gtja191_134",
    "theme": ["momentum", "volume"],
    "formula_latex": "(close-delay(close,12))/delay(close,12)*volume",
    "columns_required": ["close", "volume"],
    "extras_required": [],
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 12,
    "min_warmup_bars": 13,
    "notes": "",
}


def compute(panel):
    """Compute gtja191_134.

    Args:
        panel: dict[str, pd.DataFrame] with at least the required columns.

    Returns:
        pd.DataFrame with index = panel["close"].index, columns = panel["close"].columns.
    """
    c = panel["close"]
    v = panel["volume"]
    out = safe_div(c - c.shift(12), c.shift(12)) * v
    return out
