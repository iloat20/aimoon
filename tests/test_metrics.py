"""Tests for metrics module and async trading framework."""

from __future__ import annotations

import asyncio
import time

import numpy as np
import pandas as pd
import pytest


class TestMetricsCollector:
    """Tests for the lightweight metrics module."""

    def should_observe_and_compute_statistics(self) -> None:
        """observe() should track values and compute mean/p95/p99."""
        from aimoon.metrics import MetricsCollector

        m = MetricsCollector()
        for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
            m.observe("test_metric", v)

        summary = m.summary()
        assert "test_metric" in summary["metrics"]
        stats = summary["metrics"]["test_metric"]
        assert stats["count"] == 5
        assert stats["mean"] == 3.0
        assert stats["last"] == 5.0
        assert stats["p95"] >= 4.0

    def should_increment_counters(self) -> None:
        """increment() should track event counts."""
        from aimoon.metrics import MetricsCollector

        m = MetricsCollector()
        m.increment("orders_filled")
        m.increment("orders_filled")
        m.increment("orders_cancelled")

        assert m.summary()["counters"]["orders_filled"] == 2
        assert m.summary()["counters"]["orders_cancelled"] == 1

    def should_time_context_manager(self) -> None:
        """timer() context manager should record elapsed time."""
        from aimoon.metrics import MetricsCollector

        m = MetricsCollector()
        with m.timer("test_operation"):
            time.sleep(0.01)

        stats = m.summary()["metrics"]["test_operation"]
        assert stats["count"] == 1
        assert stats["last"] >= 0.005

    def should_format_summary_text(self) -> None:
        """summary_text() should produce readable output."""
        from aimoon.metrics import MetricsCollector

        m = MetricsCollector()
        m.observe("latency", 0.5)
        m.increment("requests", 10)

        text = m.summary_text()
        assert "latency" in text
        assert "requests" in text

    def should_singleton_global(self) -> None:
        """get_metrics() should return the same instance."""
        from aimoon.metrics import get_metrics

        m1 = get_metrics()
        m2 = get_metrics()
        assert m1 is m2

    def should_record_backtest_metrics(self) -> None:
        """record_backtest_metrics() should populate all fields."""
        from aimoon.metrics import get_metrics
        from aimoon.metrics import record_backtest_metrics

        m = get_metrics()
        record_backtest_metrics(
            duration_seconds=12.5,
            n_stocks=200,
            n_days=180,
            total_return=15.5,
            sharpe=1.8,
            max_drawdown=8.2,
            win_rate=0.6,
            n_trades=45,
        )

        s = m.summary()
        assert "backtest_duration_seconds" in s["metrics"]
        assert s["counters"]["backtest_runs"] >= 1


class TestSignalEngine:
    """Tests for async signal computation."""

    def should_compute_signals_async(self) -> None:
        """compute_signals() should return a score dict."""
        from aimoon.async_trading import SignalEngine

        engine = SignalEngine()

        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=100, freq="B")
        kline = pd.DataFrame({
            "close": np.cumsum(np.random.randn(100) * 0.02 + 0.001) + 10,
            "high": np.random.randn(100) * 0.5 + 12,
            "low": np.random.randn(100) * 0.5 + 8,
            "volume": np.random.randint(100000, 10000000, 100).astype(float),
            "pct_change": np.random.randn(100) * 0.02,
            "turnover": np.random.randn(100) * 0.5,
            "amount": np.random.randn(100) * 1e6,
            "amplitude": np.random.randn(100),
            "change": np.random.randn(100),
        }, index=dates)

        result = asyncio.run(engine.compute_signals("TEST", kline))
        assert isinstance(result, dict)
        assert "score" in result
        assert 0 <= result["score"] <= 100

    def should_compute_batch_async(self) -> None:
        """compute_batch() should process multiple stocks."""
        from aimoon.async_trading import SignalEngine

        engine = SignalEngine()
        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=100, freq="B")

        klines = {}
        for code in ["AAA", "BBB", "CCC"]:
            klines[code] = pd.DataFrame({
                "close": np.cumsum(np.random.randn(100) * 0.02 + 0.001) + 10,
                "high": np.random.randn(100) * 0.5 + 12,
                "low": np.random.randn(100) * 0.5 + 8,
                "volume": np.random.randint(100000, 10000000, 100).astype(float),
                "pct_change": np.random.randn(100) * 0.02,
                "turnover": np.random.randn(100) * 0.5,
                "amount": np.random.randn(100) * 1e6,
                "amplitude": np.random.randn(100),
                "change": np.random.randn(100),
            }, index=dates)

        results = asyncio.run(engine.compute_batch(klines, max_concurrent=3))
        assert len(results) == 3
        for code, data in results.items():
            assert "score" in data


class TestOrderManager:
    """Tests for async order management."""

    def should_submit_and_fill_order(self) -> None:
        """submit() should fill orders with retry."""
        from aimoon.async_trading import Order, OrderManager

        manager = OrderManager(max_retries=2, retry_delay=0.01)
        order = Order(code="TEST", action="buy", price=10.0, shares=100)
        result = asyncio.run(manager.submit(order))

        assert result.is_ok()
        filled = result.unwrap()
        assert filled.filled
        assert filled.fill_price == 10.0

    def should_track_pending_and_filled(self) -> None:
        """OrderManager should track order state."""
        from aimoon.async_trading import OrderManager

        manager = OrderManager()
        assert len(manager.pending_orders) == 0
        assert len(manager.filled_orders) == 0


class TestAsyncTradingFramework:
    """Tests for the async trading framework."""

    def should_create_and_get_status(self) -> None:
        """Framework should initialize with correct defaults."""
        from aimoon.async_trading import AsyncTradingFramework

        framework = AsyncTradingFramework(engine=None, config=None)
        status = framework.get_status()
        assert status["running"] is False
        assert status["positions"] == 0

    def should_update_klines(self) -> None:
        """update_klines() should store kline data."""
        from aimoon.async_trading import AsyncTradingFramework

        framework = AsyncTradingFramework(engine=None, config=None)
        klines = {"AAA": pd.DataFrame({"close": [1.0, 2.0, 3.0]})}
        framework.update_klines(klines)
        assert "AAA" in framework._klines

    def should_stop_gracefully(self) -> None:
        """stop() should set running to False."""
        from aimoon.async_trading import AsyncTradingFramework

        framework = AsyncTradingFramework(engine=None, config=None)
        framework.stop()
        assert framework._running is False
