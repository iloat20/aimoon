
"""Tests for market regime detection."""
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest
from aimoon.regime_enhanced import EnhancedMarketRegime, RegimeScore, detect_regime


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


def _make_regime(state: str) -> EnhancedMarketRegime:
    return EnhancedMarketRegime(
        state=state,
        confidence=1.0,
        scores=RegimeScore(volatility=0.5, trend=0.0, momentum=0.0, sentiment=0.5, structure=0.5),
        details={},
        transition_prob={},
    )


class TestDetectRegime:
    def test_short_data_returns_sideways(self):
        df = _make_kline(n=30)
        regime = detect_regime(df)
        assert isinstance(regime, EnhancedMarketRegime)
        assert regime.state == "sideways"

    def test_strong_uptrend(self):
        df = _make_kline(n=200, trend=0.003, vol=0.015)
        regime = detect_regime(df)
        assert isinstance(regime, EnhancedMarketRegime)
        assert regime.state in ("bull", "sideways")

    def test_strong_downtrend(self):
        df = _make_kline(n=200, trend=-0.003, vol=0.015)
        regime = detect_regime(df)
        assert isinstance(regime, EnhancedMarketRegime)
        assert regime.state in ("bear", "sideways")

    def test_high_volatility(self):
        df = _make_kline(n=200, trend=-0.001, vol=0.05)
        regime = detect_regime(df)
        assert isinstance(regime, EnhancedMarketRegime)

    def test_is_trending(self):
        assert _make_regime("bull").is_trending is True
        assert _make_regime("bear").is_trending is True
        assert _make_regime("sideways").is_trending is False

    def test_is_risky(self):
        assert _make_regime("bear").is_risky is True
        assert _make_regime("high_volatility").is_risky is True
        assert _make_regime("bull").is_risky is False

    def test_position_scale(self):
        assert _make_regime("bull").position_scale == 1.0
        assert _make_regime("bear").position_scale == 0.3
