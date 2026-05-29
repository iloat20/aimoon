"""Tests for portfolio backtest engine"""
import numpy as np
import pandas as pd
from aimoon.backtest import BacktestEngine


def _make_kline(n: int = 200, trend: float = 0.001) -> pd.DataFrame:
    np.random.seed(42)
    close = 100 * np.exp(np.cumsum(np.random.normal(trend, 0.02, n)))
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "close": close,
        "high": close * 1.01,
        "low": close * 0.99,
        "volume": np.random.randint(1000, 10000, n).astype(float),
        "pct_change": np.concatenate([[0], np.diff(close) / close[:-1] * 100]),
        "turnover": np.random.uniform(3, 10, n),
    }, index=idx)


class TestPortfolioBacktest:
    def test_single_stock(self) -> None:
        engine = BacktestEngine(hold_days=5)
        kline = _make_kline(200)
        result = engine.run_single("TEST", "Test", kline)
        assert result.code == "TEST"
        assert isinstance(result.total_return, float)

    def test_portfolio_basic(self) -> None:
        engine = BacktestEngine(hold_days=5, max_positions=2)
        klines = {"A": _make_kline(200), "B": _make_kline(200, trend=0.003)}
        names = {"A": "StockA", "B": "StockB"}
        result = engine.run_portfolio(klines, names)
        assert isinstance(result.total_return, float)
        assert isinstance(result.equity_curve, tuple)
        assert len(result.equity_curve) >= 1

    def test_empty_portfolio(self) -> None:
        engine = BacktestEngine()
        result = engine.run_portfolio({}, {})
        assert result.total_return == 0.0
        assert result.trade_count == 0

    def test_transaction_costs_reduce_return(self) -> None:
        # 高交易成本应降低收益
        cheap = BacktestEngine(hold_days=5, commission=0.0, slippage=0.0, stamp_tax=0.0)
        expensive = BacktestEngine(hold_days=5, commission=0.01, slippage=0.01, stamp_tax=0.01)
        kline = _make_kline(200, trend=0.005)
        r_cheap = cheap.run_single("T", "T", kline)
        r_exp = expensive.run_single("T", "T", kline)
        # 有成本的收益应该 <= 无成本的收益
        assert r_exp.total_return <= r_cheap.total_return + 0.01
