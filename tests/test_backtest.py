"""Tests for backtest engine"""
import numpy as np
import pandas as pd
import pytest
from aimoon.config import Config
from aimoon.backtest import BacktestEngine, BacktestResult


@pytest.fixture
def trending_kline() -> pd.DataFrame:
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    close = 10 + np.arange(n) * 0.1 + np.random.randn(n) * 0.2
    close = np.maximum(close, 1.0)
    return pd.DataFrame({
        "open": close + np.random.randn(n) * 0.05,
        "close": close, "high": close + np.abs(np.random.randn(n) * 0.1),
        "low": close - np.abs(np.random.randn(n) * 0.1),
        "volume": np.random.randint(100000, 1000000, n).astype(float),
        "turnover": np.random.uniform(1, 10, n),
        "pct_change": np.random.randn(n) * 2,
    }, index=dates)


class TestBacktestEngine:
    def test_returns_result(self, trending_kline: pd.DataFrame) -> None:
        engine = BacktestEngine(Config(), hold_days=5)
        result = engine.run("000001", "Test", trending_kline)
        assert isinstance(result, BacktestResult)

    def test_short_data_no_trades(self) -> None:
        engine = BacktestEngine(Config(), hold_days=5)
        df = pd.DataFrame({"close": [10.0] * 30}, index=pd.date_range("2025-01-01", periods=30))
        result = engine.run("000001", "Test", df)
        assert result.trade_count == 0
