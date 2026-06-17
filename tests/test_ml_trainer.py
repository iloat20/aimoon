"""Test ML trainer module."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aimoon.ml.trainer import TrainingResult, _collect_training_data, _default_params


def _make_dummy_panel(n_stocks: int = 20, n_days: int = 100) -> dict[str, pd.DataFrame]:
    codes = [f"{i:06d}" for i in range(n_stocks)]
    dates = pd.date_range(end="2026-06-01", periods=n_days, freq="B")
    data = {}
    for col in ("open", "high", "low", "close", "volume"):
        arr = np.random.randn(n_days, n_stocks).cumsum(axis=0) + 100
        arr = np.abs(arr)  # Ensure positive
        data[col] = pd.DataFrame(arr, index=dates, columns=codes)
    return data


def _make_klines(panel: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    close = panel["close"]
    klines = {}
    for code in close.columns:
        klines[code] = pd.DataFrame({"close": close[code]}, index=close.index)
    return klines


def test_default_params():
    params = _default_params()
    assert params["objective"] == "reg:pseudohubererror"
    assert params["max_depth"] == 3
    assert params["learning_rate"] == 0.01
    assert params.get("eval_metric") == "rmse"


def test_collect_training_data_insufficient_panel():
    empty_panel: dict[str, pd.DataFrame] = {}
    klines: dict[str, pd.DataFrame] = {}
    X, y, meta = _collect_training_data(empty_panel, klines, None)
    assert X.empty
    assert len(y) == 0


def test_collect_training_data_short_panel():
    short_panel = _make_dummy_panel(20, 15)  # < 20 rows
    klines = _make_klines(short_panel)
    X, y, meta = _collect_training_data(short_panel, klines, None)
    assert X.empty


def test_training_result_fields():
    result = TrainingResult(
        model=None,
        feature_names=["f1", "f2"],
        feature_importance={"f1": 0.6, "f2": 0.4},
        ic=0.15,
        n_stocks=100,
        n_dates=5,
        train_duration=1.5,
    )
    assert result.ic == 0.15
    assert result.n_stocks == 100
    assert len(result.feature_names) == 2


@pytest.mark.skipif(
    not pytest.importorskip("xgboost", reason="xgboost not installed"),
    reason="xgboost not installed",
)
def test_train_model_insufficient_data():
    from aimoon.ml.trainer import train_model

    panel = _make_dummy_panel(5, 30)
    klines = _make_klines(panel)
    with pytest.raises(ValueError, match="Insufficient training data"):
        train_model(panel, klines, n_dates=3, forward_days=5)
