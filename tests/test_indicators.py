"""Tests for technical indicators"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aimoon.indicators.technical import TechnicalIndicators


@pytest.fixture
def sample_kline() -> pd.DataFrame:
    np.random.seed(42)
    n = 100
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    close = 10 + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame({
        "open": close + np.random.randn(n) * 0.1,
        "close": close,
        "high": close + np.abs(np.random.randn(n) * 0.3),
        "low": close - np.abs(np.random.randn(n) * 0.3),
        "volume": np.random.randint(100000, 1000000, n).astype(float),
        "turnover": np.random.uniform(1, 10, n),
        "pct_change": np.random.randn(n) * 2,
    }, index=dates)
    df.index.name = "date"
    return df


class TestTechnicalIndicators:
    def test_ma_calculation(self, sample_kline: pd.DataFrame) -> None:
        ti = TechnicalIndicators(sample_kline)
        ma5 = ti.ma(5)
        assert len(ma5) == len(sample_kline)
        assert ma5.iloc[:4].isna().all()
        assert not pd.isna(ma5.iloc[-1])

    def test_ema_calculation(self, sample_kline: pd.DataFrame) -> None:
        ti = TechnicalIndicators(sample_kline)
        ema12 = ti.ema(12)
        assert len(ema12) == len(sample_kline)
        assert not pd.isna(ema12.iloc[-1])

    def test_rsi_range(self, sample_kline: pd.DataFrame) -> None:
        ti = TechnicalIndicators(sample_kline)
        rsi = ti.rsi()
        valid = rsi.dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_rsi_signal(self, sample_kline: pd.DataFrame) -> None:
        ti = TechnicalIndicators(sample_kline)
        sig = ti.rsi_signal()
        assert sig in ("oversold", "overbought", "neutral")

    def test_macd_returns_three_series(self, sample_kline: pd.DataFrame) -> None:
        ti = TechnicalIndicators(sample_kline)
        dif, dea, hist = ti.macd()
        assert len(dif) == len(sample_kline)
        assert len(dea) == len(sample_kline)
        assert len(hist) == len(sample_kline)

    def test_kdj_returns_three_series(self, sample_kline: pd.DataFrame) -> None:
        ti = TechnicalIndicators(sample_kline)
        k, d, j = ti.kdj()
        assert len(k) == len(sample_kline)
        assert len(d) == len(sample_kline)
        assert len(j) == len(sample_kline)

    def test_bollinger_returns_three_series(self, sample_kline: pd.DataFrame) -> None:
        ti = TechnicalIndicators(sample_kline)
        upper, mid, lower = ti.bollinger()
        assert len(upper) == len(sample_kline)
        assert (upper.dropna() >= mid.dropna()).all()
        assert (mid.dropna() >= lower.dropna()).all()

    def test_volume_ratio_positive(self, sample_kline: pd.DataFrame) -> None:
        ti = TechnicalIndicators(sample_kline)
        vr = ti.volume_ratio()
        assert vr > 0

    def test_add_all_indicators(self, sample_kline: pd.DataFrame) -> None:
        ti = TechnicalIndicators(sample_kline)
        df = ti.add_all_indicators()
        expected_cols = ["ma5", "ma10", "ma20", "ma60", "rsi14",
                         "macd_dif", "macd_dea", "macd_hist",
                         "kdj_k", "kdj_d", "kdj_j",
                         "boll_upper", "boll_mid", "boll_lower", "vol_ratio"]
        for col in expected_cols:
            assert col in df.columns, f"Missing column: {col}"
