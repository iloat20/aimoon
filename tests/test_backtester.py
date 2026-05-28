"""Tests for backtesting engine"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aimoon.strategies.backtester import BacktestEngine, BacktestResult
from aimoon.strategies.technical import TechnicalStrategy


@pytest.fixture
def trending_kline() -> pd.DataFrame:
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    close = 10 + np.arange(n) * 0.1 + np.random.randn(n) * 0.2
    close = np.maximum(close, 1.0)
    return pd.DataFrame({
        "open": close + np.random.randn(n) * 0.05,
        "close": close,
        "high": close + np.abs(np.random.randn(n) * 0.1),
        "low": close - np.abs(np.random.randn(n) * 0.1),
        "volume": np.random.randint(100000, 1000000, n).astype(float),
        "turnover": np.random.uniform(1, 10, n),
        "pct_change": np.random.randn(n) * 2,
    }, index=dates)


class TestBacktestEngine:
    def test_run_returns_result(self, trending_kline: pd.DataFrame) -> None:
        engine = BacktestEngine(TechnicalStrategy(), hold_days=5)
        result = engine.run("000001", "Test", trending_kline)
        assert isinstance(result, BacktestResult)
        assert result.stock_code == "000001"

    def test_short_data_no_trades(self) -> None:
        engine = BacktestEngine(TechnicalStrategy(), hold_days=5)
        df = pd.DataFrame({"close": [10.0] * 30}, index=pd.date_range("2025-01-01", periods=30))
        result = engine.run("000001", "Test", df)
        assert result.trade_count == 0

    def test_batch_run(self, trending_kline: pd.DataFrame) -> None:
        engine = BacktestEngine(TechnicalStrategy(), hold_days=5)
        stocks = {"000001": ("Stock1", trending_kline), "000002": ("Stock2", trending_kline)}
        results = engine.run_batch(stocks)
        assert len(results) == 2

    def test_metrics_calculation(self, trending_kline: pd.DataFrame) -> None:
        engine = BacktestEngine(TechnicalStrategy(), hold_days=5)
        result = engine.run("000001", "Test", trending_kline)
        assert 0.0 <= result.win_rate <= 1.0
        assert 0.0 <= result.max_drawdown <= 1.0
        assert result.trade_count >= 0
