"""GTJA Alpha 171 (国泰君安 191 短周期交易型 alpha 因子, 2014).

Formula (verbatim from the report):
    ((-1*((LOW-CLOSE)*(OPEN^5)))/((CLOSE-HIGH)*(CLOSE^5)))

Notes:
"""

from __future__ import annotations

from aimoon.factors.base import (
    safe_div,
)

ALPHA_ID = "gtja191_171"

__alpha_meta__ = {
    "id": "gtja191_171",
    "theme": ["microstructure"],
    "formula_latex": "-1*((l-c)*(o^5))/((c-h)*(c^5))",
    "columns_required": ["open", "high", "low", "close"],
    "extras_required": [],
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 1,
    "min_warmup_bars": 1,
    "notes": "",
}


def compute(panel):
    """Compute gtja191_171.

    Args:
        panel: dict[str, pd.DataFrame] with at least the required columns.

    Returns:
        pd.DataFrame with index = panel["close"].index, columns = panel["close"].columns.
    """
    c = panel["close"]
    o = panel["open"]
    h = panel["high"]
    l = panel["low"]
    out = safe_div(-1.0 * ((l - c) * (o**5)), (c - h) * (c**5))
    return out
