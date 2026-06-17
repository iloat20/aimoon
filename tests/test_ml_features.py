"""Test ML feature extraction."""
from __future__ import annotations

import numpy as np
import pandas as pd

from aimoon.ml.feature_pipeline import extract_features


def _make_dummy_panel(n_stocks: int = 10, n_days: int = 100) -> dict[str, pd.DataFrame]:
    codes = [f"{i:06d}" for i in range(n_stocks)]
    dates = pd.date_range(end="2026-06-01", periods=n_days, freq="B")
    data = {}
    for col in ("open", "high", "low", "close", "volume"):
        arr = np.random.randn(n_days, n_stocks).cumsum(axis=0) + 100
        data[col] = pd.DataFrame(arr, index=dates, columns=codes)
    return data


def test_extract_features_empty_panel():
    result = extract_features(None)
    assert result.empty


def test_extract_features_no_close():
    result = extract_features({"volume": pd.DataFrame()})
    assert result.empty


def test_extract_features_too_few_stocks():
    panel = _make_dummy_panel(3, 100)
    result = extract_features(panel)
    assert result.empty


def test_extract_features_shape():
    panel = _make_dummy_panel(10, 100)
    result = extract_features(panel)
    assert not result.empty
    assert len(result) <= 10
    assert result.shape[1] >= 2  # At least basic features


def test_extract_features_no_nan():
    panel = _make_dummy_panel(10, 100)
    result = extract_features(panel)
    assert not result.isna().any().any()


def test_extract_features_with_target_date():
    panel = _make_dummy_panel(10, 100)
    target = panel["close"].index[-10]
    result = extract_features(panel, target_date=target)
    assert not result.empty
    assert len(result) <= 10
