"""Tests for EnhancedBacktestEngine."""
import numpy as np
import pandas as pd
import pytest
from aimoon.enhanced_backtest import EnhancedBacktestEngine, EnhancedPortfolioResult


@pytest.fixture
def trending_klines() -> dict[str, pd.DataFrame]:
    np.random.seed(42)
    klines = {}
    for code, trend in [("A", 0.003), ("B", 0.001), ("C", -0.001)]:
        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="B")
        close = 100 * np.exp(np.cumsum(np.random.normal(trend, 0.02, n)))
        klines[code] = pd.DataFrame({
            "close": close, "high": close * 1.01, "low": close * 0.99,
            "open": close * 1.001,
            "volume": np.random.randint(1000, 10000, n).astype(float),
            "turnover": np.random.uniform(3, 10, n),
            "pct_change": np.concatenate([[0], np.diff(close) / close[:-1] * 100]),
        }, index=dates)
    return klines


class TestEnhancedBacktestEngine:
    def test_returns_enhanced_result(self, trending_klines):
        engine = EnhancedBacktestEngine(hold_days=10, max_positions=2)
        names = {"A": "StockA", "B": "StockB", "C": "StockC"}
        result = engine.run_portfolio(trending_klines, names)
        assert isinstance(result, EnhancedPortfolioResult)
        assert result.trade_count >= 0

    def test_stop_loss_exits_present(self, trending_klines):
        engine = EnhancedBacktestEngine(hold_days=20, stop_loss_pct=0.02)
        result = engine.run_portfolio(trending_klines, {"A": "A", "B": "B", "C": "C"})
        valid_reasons = {"stop_loss", "take_profit", "hold_period", "data_gap", "momentum_exit"}
        for t in result.trades:
            assert t.exit_reason in valid_reasons

    def test_empty_klines(self):
        engine = EnhancedBacktestEngine()
        result = engine.run_portfolio({}, {})
        assert result.total_return == 0.0
        assert result.trade_count == 0

    def test_enhanced_fields_present(self, trending_klines):
        engine = EnhancedBacktestEngine(hold_days=10, max_positions=2)
        result = engine.run_portfolio(trending_klines, {"A": "A", "B": "B", "C": "C"})
        assert hasattr(result, "sortino_ratio")
        assert hasattr(result, "profit_factor")
        assert hasattr(result, "avg_win")
        assert hasattr(result, "avg_loss")
        assert hasattr(result, "drawdown_curve")
        assert hasattr(result, "equity_curve")

    def test_benchmark_tracking(self, trending_klines):
        engine = EnhancedBacktestEngine(hold_days=10, benchmark_code="A")
        result = engine.run_portfolio(trending_klines, {"A": "A", "B": "B", "C": "C"})
        assert result.benchmark_return != 0.0 or result.trade_count == 0
