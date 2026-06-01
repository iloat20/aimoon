"""Tests for fundamental scorer."""
import numpy as np
import pandas as pd
import pytest
from aimoon.scoring.fundamentals import score_fundamentals
from aimoon.indicators.technical import TechInd


@pytest.fixture
def dummy_kline():
    n = 100
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    close = np.linspace(10, 20, n)
    return pd.DataFrame({
        "close": close, "high": close * 1.01, "low": close * 0.99,
        "open": close, "volume": np.full(n, 1e6),
    }, index=dates)


class TestScoreFundamentals:
    def test_low_pe_bullish(self, dummy_kline):
        ti = TechInd(dummy_kline)
        ctx = {"spot_row": {"pe": 12.0, "pb": 1.5}}
        signals = score_fundamentals(ti, code="000001", ctx=ctx)
        assert signals is not None
        names = [s.name for s in signals]
        assert "pe_low" in names
        assert "pb_low" in names

    def test_high_pe_bearish(self, dummy_kline):
        ti = TechInd(dummy_kline)
        ctx = {"spot_row": {"pe": 100.0, "pb": 15.0}}
        signals = score_fundamentals(ti, code="000001", ctx=ctx)
        assert signals is not None
        names = [s.name for s in signals]
        assert "pe_high" in names
        assert "pb_high" in names

    def test_negative_pe(self, dummy_kline):
        ti = TechInd(dummy_kline)
        ctx = {"spot_row": {"pe": -5.0, "pb": 2.0}}
        signals = score_fundamentals(ti, code="000001", ctx=ctx)
        assert signals is not None
        names = [s.name for s in signals]
        assert "pe_negative" in names

    def test_no_spot_row_returns_none(self, dummy_kline):
        ti = TechInd(dummy_kline)
        assert score_fundamentals(ti, code="000001", ctx=None) is None
        assert score_fundamentals(ti, code="000001", ctx={}) is None

    def test_zero_pe_pb_returns_none(self, dummy_kline):
        ti = TechInd(dummy_kline)
        ctx = {"spot_row": {"pe": 0.0, "pb": 0.0}}
        assert score_fundamentals(ti, code="000001", ctx=ctx) is None

    def test_mid_pe(self, dummy_kline):
        ti = TechInd(dummy_kline)
        ctx = {"spot_row": {"pe": 30.0, "pb": 5.0}}
        signals = score_fundamentals(ti, code="000001", ctx=ctx)
        assert signals is not None
        names = [s.name for s in signals]
        assert "pe_mid" in names
        # pb=5 is not low (<3) and not high (>10), so no pb signal
        assert not any(n.startswith("pb_") for n in names)
