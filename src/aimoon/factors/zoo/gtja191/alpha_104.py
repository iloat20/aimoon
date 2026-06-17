"""GTJA Alpha 104 (国泰君安 191 短周期交易型 alpha 因子, 2014).

Formula (verbatim from the report):
    (-1 * (DELTA(CORR(high, volume,5),5) * RANK(STD(close,20))))

Notes:
"""

from __future__ import annotations

from aimoon.factors.base import (
    delta,
    rank,
    ts_corr,
    ts_std,
)

ALPHA_ID = "gtja191_104"

__alpha_meta__ = {
    "id": "gtja191_104",
    "theme": ["volume", "volatility"],
    "formula_latex": "-1*delta(corr(high,volume,5),5)*rank(std(close,20))",
    "columns_required": ["open", "high", "low", "close", "volume"],
    "extras_required": [],
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 20,
    "min_warmup_bars": 25,
    "notes": "",
}


def compute(panel):
    """Compute gtja191_104.

    Args:
        panel: dict[str, pd.DataFrame] with at least the required columns.

    Returns:
        pd.DataFrame with index = panel["close"].index, columns = panel["close"].columns.
    """
    c = panel["close"]
    h = panel["high"]
    v = panel["volume"]
    corr_hv = ts_corr(h, v, 5)
    out = -1.0 * (delta(corr_hv, 5) * rank(ts_std(c, 20)))
    return out
