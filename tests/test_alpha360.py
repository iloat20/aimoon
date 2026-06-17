"""Test Alpha360 time-series feature extraction."""
from __future__ import annotations

import numpy as np
import pandas as pd

from aimoon.ml.alpha360 import extract_alpha360_features


def _make_panel(n_stocks: int = 10, n_days: int = 100) -> dict[str, pd.DataFrame]:
    codes = [f"{i:06d}" for i in range(n_stocks)]
    dates = pd.date_range(end="2026-06-01", periods=n_days, freq="B")
    data = {}
    for col in ("open", "high", "low", "close", "volume"):
        arr = np.abs(np.random.randn(n_days, n_stocks).cumsum(axis=0) + 100)
        data[col] = pd.DataFrame(arr, index=dates, columns=codes)
    return data


def test_alpha360_empty_panel():
    result = extract_alpha360_features(None)
    assert result.empty


def test_alpha360_short_data():
    panel = _make_panel(10, 30)  # < 60+5 rows
    result = extract_alpha360_features(panel)
    assert result.empty


def test_alpha360_too_few_stocks():
    panel = _make_panel(3, 100)
    result = extract_alpha360_features(panel)
    assert result.empty


def test_alpha360_basic():
    panel = _make_panel(10, 100)
    result = extract_alpha360_features(panel)
    assert not result.empty
    assert len(result) == 10
    # 4 price cols × 60 + volume × 60 = 300 (no vwap available)
    assert result.shape[1] >= 300


def test_alpha360_no_nan():
    panel = _make_panel(10, 100)
    result = extract_alpha360_features(panel)
    assert not result.isna().any().any()


def test_alpha360_price_normalized():
    """a360_close_0 should contain normalized price values."""
    panel = _make_panel(10, 100)
    result = extract_alpha360_features(panel)
    assert "a360_close_0" in result.columns
    vals = result["a360_close_0"].values
    assert np.all(np.isfinite(vals))
    assert np.all(vals > 0)


def test_alpha360_with_target_date():
    panel = _make_panel(10, 100)
    target = panel["close"].index[-20]
    result = extract_alpha360_features(panel, target_date=target)
    assert not result.empty
    assert len(result) == 10
