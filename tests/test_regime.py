
"""Tests for market regime detection."""
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest
from aimoon.regime import MarketRegime, detect_regime


def _make_kline(n=200, trend=0.0, vol=0.02):
    np.random.seed(42)
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    close = 100 * np.exp(np.cumsum(np.random.normal(trend, vol, n)))
    return pd.DataFrame({
        "close": close,
        "high": close * (1 + np.abs(np.random.normal(0, 0.01, n))),
        "low": close * (1 - np.abs(np.random.normal(0, 0.01, n))),
        "volume": np.random.randint(1000, 10000, n).astype(float),
    }, index=dates)


class TestDetectRegime:
    def test_short_data_returns_sideways(self):
        df = _make_kline(n=30)
        regime = detect_regime(df)
        assert regime.state == MarketRegime.SIDEWAYS
        assert regime.confidence == 0.0

    def test_strong_uptrend(self):
        df = _make_kline(n=200, trend=0.003, vol=0.015)
        regime = detect_regime(df)
        assert regime.state in (MarketRegime.BULL, MarketRegime.SIDEWAYS)

    def test_strong_downtrend(self):
        df = _make_kline(n=200, trend=-0.003, vol=0.015)
        regime = detect_regime(df)
        assert regime.state in (MarketRegime.BEAR, MarketRegime.SIDEWAYS)

    def test_high_volatility(self):
        df = _make_kline(n=200, trend=-0.001, vol=0.05)
        regime = detect_regime(df)
        assert isinstance(regime, MarketRegime)
        assert 0.0 <= regime.confidence <= 1.0

    def test_is_trending(self):
        assert MarketRegime(MarketRegime.BULL, 0.8, {}).is_trending is True
        assert MarketRegime(MarketRegime.BEAR, 0.8, {}).is_trending is True
        assert MarketRegime(MarketRegime.SIDEWAYS, 0.5, {}).is_trending is False

    def test_is_risky(self):
        assert MarketRegime(MarketRegime.BEAR, 0.8, {}).is_risky is True
        assert MarketRegime(MarketRegime.HIGH_VOL, 0.8, {}).is_risky is True
        assert MarketRegime(MarketRegime.BULL, 0.8, {}).is_risky is False

    def test_repr(self):
        regime = MarketRegime(MarketRegime.BULL, 0.85, {})
        r = repr(regime)
        assert "bull" in r
        assert "85%" in r
