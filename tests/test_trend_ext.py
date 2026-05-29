"""Tests for extended trend factors"""
import numpy as np
import pandas as pd
from aimoon.indicators.technical import TechInd
from aimoon.scoring.trend_ext import score_trend_ext


def _make_kline(n: int = 120, trend: float = 0.001) -> pd.DataFrame:
    np.random.seed(42)
    close = 100 * np.exp(np.cumsum(np.random.normal(trend, 0.02, n)))
    return pd.DataFrame({
        "close": close,
        "high": close * (1 + np.abs(np.random.normal(0, 0.01, n))),
        "low": close * (1 - np.abs(np.random.normal(0, 0.01, n))),
        "volume": np.random.randint(1000, 10000, n).astype(float),
    })


class TestTrendExt:
    def test_returns_list(self) -> None:
        ti = TechInd(_make_kline(120))
        result = score_trend_ext(ti, code="TEST")
        assert isinstance(result, list)

    def test_bullish_trend_signals(self) -> None:
        ti = TechInd(_make_kline(120, trend=0.005))
        signals = score_trend_ext(ti, code="TEST")
        for s in signals:
            assert isinstance(s.score, int)

    def test_short_data_no_crash(self) -> None:
        ti = TechInd(_make_kline(10))
        signals = score_trend_ext(ti, code="TEST")
        assert isinstance(signals, list)
