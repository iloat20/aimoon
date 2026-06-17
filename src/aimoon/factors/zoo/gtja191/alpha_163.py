"""GTJA Alpha 163 (国泰君安 191 短周期交易型 alpha 因子, 2014).

Formula (verbatim from the report):
    RANK(((((-1 * RET) * MEAN(VOLUME,20)) * VWAP) * (HIGH - CLOSE)))

Notes:
"""

from __future__ import annotations

from aimoon.factors.base import (
    rank,
    safe_div,
    ts_mean,
    vwap,
)

ALPHA_ID = "gtja191_163"

__alpha_meta__ = {
    "id": "gtja191_163",
    "theme": ["volume"],
    "formula_latex": "rank(((-1*ret)*mean(v,20))*vwap*(high-close))",
    "columns_required": ["open", "high", "low", "close", "volume", "amount"],
    "extras_required": [],
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 20,
    "min_warmup_bars": 21,
    "notes": "",
}


def compute(panel):
    """Compute gtja191_163.

    Args:
        panel: dict[str, pd.DataFrame] with at least the required columns.

    Returns:
        pd.DataFrame with index = panel["close"].index, columns = panel["close"].columns.
    """
    c = panel["close"]
    h = panel["high"]
    v = panel["volume"]
    vw = vwap(panel, "equity_cn")

    ret = safe_div(c, c.shift(1)) - 1.0
    out = rank(((-1.0 * ret) * ts_mean(v, 20)) * vw * (h - c))
    return out
