"""GTJA Alpha 151 (国泰君安 191 短周期交易型 alpha 因子, 2014).

Formula (verbatim from the report):
    SMA(CLOSE-DELAY(CLOSE,20),20,1)

Notes:
"""

from __future__ import annotations

ALPHA_ID = "gtja191_151"

__alpha_meta__ = {
    "id": "gtja191_151",
    "theme": ["momentum"],
    "formula_latex": "sma(close-delay(close,20),20,1)",
    "columns_required": ["close"],
    "extras_required": [],
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 20,
    "min_warmup_bars": 21,
    "notes": "",
}


def compute(panel):
    """Compute gtja191_151.

    Args:
        panel: dict[str, pd.DataFrame] with at least the required columns.

    Returns:
        pd.DataFrame with index = panel["close"].index, columns = panel["close"].columns.
    """

    def _sma(x, n, m):
        """SMA(x, n, m) per GTJA convention -> ewm with alpha = m/n."""
        return x.ewm(alpha=m / n, adjust=False).mean()

    c = panel["close"]
    out = _sma(c - c.shift(20), 20, 1)
    return out
