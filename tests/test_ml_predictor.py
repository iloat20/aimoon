"""Test ML predictor module."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aimoon.ml.predictor import predict_alpha_signals


def test_predict_empty_panel():
    result = predict_alpha_signals(None, None)
    assert result == {}


def test_predict_no_close():
    result = predict_alpha_signals(None, {"volume": pd.DataFrame()})
    assert result == {}


@pytest.mark.skipif(
    not pytest.importorskip("xgboost", reason="xgboost not installed"),
    reason="xgboost not installed",
)
def test_predict_missing_model_files():
    """Test that predict returns empty when feature_names.json is missing."""
    import xgboost as xgb

    # Create a minimal model
    model = xgb.Booster()
    dtrain = xgb.DMatrix(
        np.random.randn(10, 3),
        label=np.random.randn(10),
    )
    model = xgb.train({"verbosity": 0}, dtrain, num_boost_round=1)

    # Panel with enough stocks but no feature_names.json
    panel = _make_dummy_panel(10, 100)
    result = predict_alpha_signals(model, panel)
    assert result == {}  # Should fail gracefully


def _make_dummy_panel(n_stocks: int = 10, n_days: int = 100) -> dict[str, pd.DataFrame]:
    codes = [f"{i:06d}" for i in range(n_stocks)]
    dates = pd.date_range(end="2026-06-01", periods=n_days, freq="B")
    data = {}
    for col in ("open", "high", "low", "close", "volume"):
        arr = np.random.randn(n_days, n_stocks).cumsum(axis=0) + 100
        data[col] = pd.DataFrame(arr, index=dates, columns=codes)
    return data
