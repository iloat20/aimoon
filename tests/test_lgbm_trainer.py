"""Test LightGBM trainer module."""
from __future__ import annotations

import pandas as pd
import pytest

from aimoon.ml.lgbm_trainer import _default_lgbm_params


def test_default_lgbm_params():
    params = _default_lgbm_params()
    assert params["objective"] == "regression"
    assert params["num_leaves"] == 21
    assert params["learning_rate"] == 0.02
    assert params["random_state"] == 42
    assert params["verbose"] == -1


@pytest.mark.skipif(
    not pytest.importorskip("lightgbm", reason="lightgbm not installed"),
    reason="lightgbm not installed",
)
def test_lgbm_train_insufficient_data():
    from aimoon.ml.lgbm_trainer import train_lgbm_model

    panel: dict[str, pd.DataFrame] = {}
    klines: dict[str, pd.DataFrame] = {}
    with pytest.raises(ValueError, match="Insufficient training data"):
        train_lgbm_model(panel, klines, n_dates=3, forward_days=5)
