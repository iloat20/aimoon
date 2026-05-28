"""Tests for stock screener and strategy system"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aimoon.strategies.screener import StockScreener, SignalScore
from aimoon.strategies.technical import TechnicalStrategy
from aimoon.strategies.base import Strategy


@pytest.fixture
def sample_kline() -> pd.DataFrame:
    np.random.seed(42)
    n = 120
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    close = 20 + np.cumsum(np.random.randn(n) * 0.5)
    close = np.maximum(close, 1.0)
    return pd.DataFrame({
        "open": close + np.random.randn(n) * 0.1,
        "close": close,
        "high": close + np.abs(np.random.randn(n) * 0.3),
        "low": close - np.abs(np.random.randn(n) * 0.3),
        "volume": np.random.randint(100000, 1000000, n).astype(float),
        "turnover": np.random.uniform(1, 10, n),
        "pct_change": np.random.randn(n) * 2,
    }, index=dates)


class TestTechnicalStrategy:
    def test_score_returns_signal(self, sample_kline: pd.DataFrame) -> None:
        strategy = TechnicalStrategy()
        result = strategy.score("000001", "Test", sample_kline)
        assert result is not None
        assert isinstance(result, SignalScore)
        assert result.stock_code == "000001"

    def test_score_short_data_returns_none(self) -> None:
        strategy = TechnicalStrategy()
        df = pd.DataFrame({"close": [10.0] * 10})
        assert strategy.score("000001", "Test", df) is None

    def test_score_with_spot_data(self, sample_kline: pd.DataFrame) -> None:
        strategy = TechnicalStrategy()
        spot = pd.Series({"pe": 15.0, "pb": 2.0, "total_market_cap": 1e10, "float_market_cap": 5e9})
        result = strategy.score("000001", "Test", sample_kline, spot)
        assert result is not None
        assert result.pe == 15.0
        assert result.pb == 2.0

    def test_name_property(self) -> None:
        assert TechnicalStrategy().name == "technical"


class TestStockScreener:
    def test_screen_stock(self, sample_kline: pd.DataFrame) -> None:
        screener = StockScreener()
        result = screener.screen_stock("000001", "Test", sample_kline)
        assert result is not None
        assert len(screener.results) == 1

    def test_get_top_picks(self, sample_kline: pd.DataFrame) -> None:
        screener = StockScreener()
        for i in range(5):
            screener.screen_stock(f"00000{i}", f"Stock{i}", sample_kline)
        picks = screener.get_top_picks(3)
        assert len(picks) == 3
        assert picks[0].total_score >= picks[1].total_score

    def test_custom_strategy(self, sample_kline: pd.DataFrame) -> None:
        class DummyStrategy(Strategy):
            @property
            def name(self) -> str:
                return "dummy"
            def score(self, code, name, kline, spot=None, market_context=None):
                return SignalScore(
                    stock_code=code, stock_name=name,
                    price=10.0, pct_change=0.0, turnover=5.0,
                    total_score=99,
                )
        screener = StockScreener(strategies=[DummyStrategy()])
        result = screener.screen_stock("000001", "Test", sample_kline)
        assert result is not None
        assert result.total_score == 99
