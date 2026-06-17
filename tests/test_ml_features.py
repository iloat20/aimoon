"""Test ML feature extraction (new simplified version)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aimoon.ml.feature_pipeline import extract_features


def _make_dummy_panel(n_stocks: int = 10, n_days: int = 100) -> dict[str, pd.DataFrame]:
    codes = [f"{i:06d}" for i in range(n_stocks)]
    dates = pd.date_range(end="2026-06-01", periods=n_days, freq="D")
    data: dict[str, pd.DataFrame] = {}
    rng = np.random.default_rng(42)
    for col in ("open", "high", "low", "close", "volume"):
        arr = rng.random((n_days, n_stocks)).cumsum(axis=0) + 100
        data[col] = pd.DataFrame(arr, index=dates, columns=codes)
    # Add turnover and amount for amihud/turnover factors
    data["turnover"] = data["volume"] * 0.0 + 0.5
    data["amount"] = data["close"] * data["turnover"] * 1e7
    return data


def test_extract_features_empty_close():
    result = extract_features({})
    assert result.empty


def test_extract_features_no_close():
    result = extract_features({"volume": pd.DataFrame()})
    assert result.empty


def test_extract_features_shape():
    panel = _make_dummy_panel(10, 120)
    result = extract_features(panel)
    assert not result.empty
    assert len(result) <= 10
    # 因子：4 价格 + 2 换手/amihud = 6 + 6 tech = 12
    assert result.shape[1] >= 10


def test_extract_features_no_nan():
    panel = _make_dummy_panel(10, 120)
    result = extract_features(panel)
    assert not result.isna().any().any()


def test_extract_features_with_target_date():
    panel = _make_dummy_panel(10, 120)
    target = panel["close"].index[-10]
    result = extract_features(panel, target_date=target)
    assert not result.empty
    assert len(result) <= 10


def test_extract_features_with_sector_map():
    panel = _make_dummy_panel(10, 120)
    codes = list(panel["close"].columns)
    sector_map = {c: "行业A" if i < 5 else "行业B" for i, c in enumerate(codes)}
    result = extract_features(panel, sector_map=sector_map)
    assert not result.empty
    # 应该有 sector_mom_20d 因子
    assert "sector_mom_20d" in result.columns


def test_extract_features_with_fundamentals():
    panel = _make_dummy_panel(10, 120)
    codes = list(panel["close"].columns)
    pe = pd.DataFrame({c: 15.0 for c in codes}, index=panel["close"].index)
    pb = pd.DataFrame({c: 2.0 for c in codes}, index=panel["close"].index)
    div = pd.DataFrame({c: 0.03 for c in codes}, index=panel["close"].index)
    fundamentals = {"pe": pe, "pb": pb, "dividend": div}
    result = extract_features(panel, fundamentals=fundamentals)
    assert not result.empty
    assert "ep" in result.columns
    assert "bp" in result.columns
    assert "div_yield" in result.columns


def test_extract_features_with_feature_medians():
    panel = _make_dummy_panel(10, 120)
    result_train = extract_features(panel)
    medians = result_train.median()
    # 推理时传入 medians
    result_infer = extract_features(panel, feature_medians=medians)
    assert not result_infer.empty
    assert not result_infer.isna().any().any()
