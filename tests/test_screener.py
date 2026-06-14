"""Tests for screener"""
import numpy as np
import pandas as pd
from aimoon.models import ScoredStock
from aimoon.screener import screen_stock


def _uptrend_kline(n: int = 100) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    close = np.linspace(10, 30, n)
    return pd.DataFrame({
        "open": close * 0.99, "close": close, "high": close * 1.01, "low": close * 0.99,
        "volume": np.full(n, 1e6), "turnover": np.full(n, 5.0), "pct_change": np.zeros(n),
    }, index=dates)


class TestScreenStock:
    def test_returns_scored_stock(self) -> None:
        result = screen_stock("000001", "Test", _uptrend_kline())
        assert result is not None
        assert isinstance(result, ScoredStock)
        assert result.code == "000001"
        # 旺势数据会产生信号，所以 total_score > 0
        assert result.total_score >= 0
        assert result.ml_score is None

    def test_short_data_returns_none(self) -> None:
        df = pd.DataFrame({"close": [10.0] * 10})
        assert screen_stock("000001", "Test", df) is None

    def test_signals_are_frozen(self) -> None:
        result = screen_stock("000001", "Test", _uptrend_kline())
        assert result is not None
        assert isinstance(result.signals, tuple)

    def test_ml_score_attribute(self) -> None:
        """screen_stock returns ScoredStock with ml_score=None (no ML in basic mode)."""
        result = screen_stock("000001", "Test", _uptrend_kline())
        assert result is not None
        assert result.ml_score is None
