"""GTJA Alpha 135 (国泰君安 191 短周期交易型 alpha 因子, 2014).

Formula (verbatim from the report):
    SMA(DELAY(CLOSE/DELAY(CLOSE,20),1),20,1)

Notes:
"""

from __future__ import annotations

from aimoon.factors.base import (
    safe_div,
)

ALPHA_ID = "gtja191_135"

__alpha_meta__ = {
    "id": "gtja191_135",
    "theme": ["momentum"],
    "formula_latex": "sma(delay(c/delay(c,20),1),20,1)",
    "columns_required": ["close"],
    "extras_required": [],
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 20,
    "min_warmup_bars": 22,
    "notes": "",
}


def compute(panel):
    """Compute gtja191_135.

    Args:
        panel: dict[str, pd.DataFrame] with at least the required columns.

    Returns:
        pd.DataFrame with index = panel["close"].index, columns = panel["close"].columns.
    """

    def _sma(x, n, m):
        """SMA(x, n, m) per GTJA convention -> ewm with alpha = m/n."""
        return x.ewm(alpha=m / n, adjust=False).mean()

    c = panel["close"]
    ratio = safe_div(c, c.shift(20)).shift(1)
    out = _sma(ratio, 20, 1)
    return out
