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
        # Controlled uptrend → RSI ≈ 65-68 (rsi_strong range ≥65)
        rng = np.random.RandomState(42)
        prices = 10 + np.cumsum(rng.normal(0.06, 0.2, 100))
        ti = _make_ti(list(prices))
        sig = score_rsi(ti)
        assert sig is not None
        assert sig.name == "rsi_strong"
        assert sig.score == 1

    def test_overbought(self) -> None:
        # Pure linear uptrend → RSI ≈ 100 (extreme overbought, ≥80)
        ti = _make_ti(list(np.linspace(10, 30, 100)))
        sig = score_rsi(ti)
        assert sig is not None
        assert sig.name == "rsi_extreme_overbought"
        assert sig.score == -2

    def test_strong_downtrend(self) -> None:
        # Controlled downtrend → RSI ≈ 35-45 (rsi_mild_bear range)
        rng = np.random.RandomState(42)
        prices = 30 + np.cumsum(rng.normal(-0.02, 0.3, 100))
        ti = _make_ti(list(prices))
        sig = score_rsi(ti)
        assert sig is not None
        assert sig.name in ("rsi_mild_bear", "rsi_slight_bear", "rsi_oversold")
        assert sig.score <= 0

    def test_oversold(self) -> None:
        # Pure linear downtrend → RSI ≈ 0 (extreme oversold, ≤20)
        ti = _make_ti(list(np.linspace(30, 5, 100)))
        sig = score_rsi(ti)
        assert sig is not None
        assert sig.name == "rsi_extreme_oversold"
        assert sig.score == 2
