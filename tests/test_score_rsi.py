"""Tests for RSI scoring"""
import numpy as np
import pandas as pd
from aimoon.indicators.technical import TechInd
from aimoon.scoring.rsi import score_rsi


def _make_ti(prices) -> TechInd:
    n = len(prices)
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    close = np.array(prices, dtype=float)
    return TechInd(pd.DataFrame({
        "open": close, "close": close, "high": close, "low": close,
        "volume": np.full(n, 1e6), "turnover": np.full(n, 5.0), "pct_change": np.zeros(n),
    }, index=dates))


class TestScoreRsi:
    def test_strong_uptrend(self) -> None:
        ti = _make_ti(list(np.linspace(10, 30, 100)))
        sig = score_rsi(ti)
        assert sig is not None
        assert sig.name == "rsi_strong"
        assert sig.score == 2

    def test_strong_downtrend(self) -> None:
        ti = _make_ti(list(np.linspace(30, 10, 100)))
        sig = score_rsi(ti)
        assert sig is not None
        assert sig.name == "rsi_weak"
        assert sig.score == -2
