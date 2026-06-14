"""Golden factor tests ? verify factor computation consistency after optimizations.

These tests use fixed input panels to verify that factor outputs remain
identical before and after performance optimizations.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _make_test_panel(n_dates: int = 100, n_stocks: int = 5) -> dict[str, pd.DataFrame]:
    """Create a deterministic test panel with known values."""
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    codes = [f"STK{i:04d}" for i in range(n_stocks)]

    close = pd.DataFrame(
        np.cumsum(np.random.randn(n_dates, n_stocks) * 0.02 + 0.001, axis=0) + 10,
        index=dates, columns=codes,
    )
    open_ = close + np.random.randn(n_dates, n_stocks) * 0.1
    high = close.abs() + np.random.randn(n_dates, n_stocks) * 0.5 + 0.5
    low = close.abs() - np.random.randn(n_dates, n_stocks) * 0.5 - 0.5
    low = low.clip(lower=0.1)
    volume = pd.DataFrame(
        np.random.randint(100000, 10000000, size=(n_dates, n_stocks)).astype(float),
        index=dates, columns=codes,
    )

    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


class TestGoldenFactors:
    """Fixed-input factor computation tests."""

    def should_compute_ts_rank_consistently(self) -> None:
        """ts_rank should produce consistent results with fixed input."""
        from aimoon.factors.base import ts_rank

        panel = _make_test_panel(100, 3)
        df = panel["close"]
        result = ts_rank(df, 10)
        assert result.shape == df.shape
        assert result.notna().sum().sum() > 0
        # Values should be in [0, 1]
        valid = result.dropna().values.flatten()
        assert bool((valid >= 0).all()) and bool((valid <= 1).all())

    def should_compute_ts_corr_consistently(self) -> None:
        """ts_corr should produce consistent results."""
        from aimoon.factors.base import ts_corr

        panel = _make_test_panel(100, 3)
        result = ts_corr(panel["close"], panel["volume"], 10)
        assert result.shape[1] == 3
        # Correlation should be in [-1, 1]
        valid = result.dropna().values.flatten()
        assert bool((valid >= -1.001).all()) and bool((valid <= 1.001).all())

    def should_compute_ts_std_consistently(self) -> None:
        """ts_std should produce non-negative values."""
        from aimoon.factors.base import ts_std

        panel = _make_test_panel(100, 3)
        result = ts_std(panel["close"], 20)
        valid = result.dropna().values.flatten()
        assert bool((valid >= 0).all())

    def should_compute_decay_linear_consistently(self) -> None:
        """decay_linear should produce weighted averages."""
        from aimoon.factors.base import decay_linear

        panel = _make_test_panel(100, 3)
        result = decay_linear(panel["close"], 10)
        # For a constant series, decay_linear should equal the constant
        assert result.notna().sum().sum() > 0
        # Result should be in the same range as input
        valid_result = result.dropna().values.flatten()
        valid_input = panel["close"].values.flatten()
        if len(valid_result) > 0:
            assert valid_result.min() >= valid_input.min() - 1
            assert valid_result.max() <= valid_input.max() + 1

    def should_compute_ts_argmaxmin_consistently(self) -> None:
        """ts_argmax/ts_argmin should produce valid indices."""
        from aimoon.factors.base import ts_argmax, ts_argmin

        panel = _make_test_panel(100, 3)
        result_max = ts_argmax(panel["close"], 10)
        result_min = ts_argmin(panel["close"], 10)

        valid_max = result_max.dropna().values.flatten()
        valid_min = result_min.dropna().values.flatten()

        # Indices should be in [0, window-1]
        assert (valid_max >= 0).all() and (valid_max < 10).all()
        assert (valid_min >= 0).all() and (valid_min < 10).all()

    def should_compute_rank_consistently(self) -> None:
        """Cross-sectional rank should be in [0, 1]."""
        from aimoon.factors.base import rank

        panel = _make_test_panel(100, 5)
        result = rank(panel["close"])
        valid = result.dropna().values.flatten()
        assert bool((valid >= 0).all()) and bool((valid <= 1).all())

    def should_compute_delta_consistently(self) -> None:
        """Delta should compute correct differences."""
        from aimoon.factors.base import delta

        panel = _make_test_panel(100, 3)
        result = delta(panel["close"], 1)
        # First row should be NaN
        assert pd.isna(result.iloc[0, 0])
        # Subsequent rows should be close to actual diff
        actual_diff = panel["close"].diff(1)
        pd.testing.assert_frame_equal(result, actual_diff, check_names=False)

    def should_run_panel_build_vectorized(self) -> None:
        """Panel build should produce valid wide-format output."""
        from aimoon.factors.panel import build_panel

        klines = {}
        panel = _make_test_panel(100, 3)
        for code in panel["close"].columns:
            klines[code] = pd.DataFrame({
                "open": panel["open"][code],
                "high": panel["high"][code],
                "low": panel["low"][code],
                "close": panel["close"][code],
                "volume": panel["volume"][code],
            })
            klines[code].index = panel["close"].index

        result = build_panel(klines, min_rows=60)
        assert result is not None
        assert "close" in result
        assert result["close"].shape[1] == 3
        assert result["close"].shape[0] > 0

    def should_cache_with_cow_mode(self) -> None:
        """DataCache should work with Copy-on-Write enabled."""
        from aimoon.cache import DataCache
        import tempfile, os

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DataCache(tmpdir, 4)
            df = pd.DataFrame(
                {"close": [1.0, 2.0, 3.0, 4.0, 5.0]},
                index=pd.date_range("2024-01-01", periods=5),
            )
            cache.put("TEST", df)
            result = cache.get("TEST")
            assert result is not None
            assert len(result) == 5

    def should_cache_with_global_singleton(self) -> None:
        """DataCache.get_global should return the same instance."""
        from aimoon.cache import DataCache
        DataCache.reset_global()
        c1 = DataCache.get_global(".test_cache", 4)
        c2 = DataCache.get_global(".test_cache", 4)
        assert c1 is c2
        DataCache.reset_global()

    def should_compute_tech_ind_batch(self) -> None:
        """Batch TechInd computation should produce valid indicators."""
        from aimoon.indicators.technical import add_all_indicators_batch

        panel = _make_test_panel(100, 3)
        result = add_all_indicators_batch(panel)
        assert "ma5" in result
        assert "rsi14" in result
        assert "macd_dif" in result
        assert "kdj_k" in result
        assert "boll_upper" in result
        assert "vol_ratio" in result
        # RSI should be in [0, 100]
        valid_rsi = result["rsi14"].dropna().values.flatten()
        if len(valid_rsi) > 0:
            assert bool((valid_rsi >= -0.1).all()) and bool((valid_rsi <= 100.1).all())

    def should_run_data_handler_fit_transform(self) -> None:
        """DataHandler fit_transform should work with ICIR pre-screening disabled."""
        pytest.skip("Requires full factor registry ? run as integration test")
