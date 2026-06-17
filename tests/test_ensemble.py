"""Test ensemble predictor module."""
from __future__ import annotations

import numpy as np
import pandas as pd

from aimoon.ml.ensemble import EnsemblePredictor, compute_optimal_weights


def test_ensemble_predictor_empty():
    predictor = EnsemblePredictor()
    result = predictor.predict(None)
    assert result.empty


def test_ensemble_predictor_no_models():
    predictor = EnsemblePredictor()
    assert not predictor.has_xgb
    assert not predictor.has_lgbm


def test_ensemble_from_cache_no_files():
    """Should return predictor with no models when cache is empty."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        predictor = EnsemblePredictor.from_cache(tmpdir)
        assert not predictor.has_xgb
        assert not predictor.has_lgbm


def test_compute_optimal_weights_basic():
    np.random.seed(42)
    n = 100
    labels = pd.Series(np.random.randn(n))
    xgb_preds = labels + np.random.randn(n) * 0.5
    lgbm_preds = labels + np.random.randn(n) * 0.3

    w_xgb, w_lgbm = compute_optimal_weights(xgb_preds, lgbm_preds, labels)
    assert 0 <= w_xgb <= 1
    assert 0 <= w_lgbm <= 1
    assert abs(w_xgb + w_lgbm - 1.0) < 1e-6


def test_compute_optimal_weights_insufficient_data():
    labels = pd.Series(np.random.randn(5))
    xgb_preds = pd.Series(np.random.randn(5))
    lgbm_preds = pd.Series(np.random.randn(5))

    w_xgb, w_lgbm = compute_optimal_weights(xgb_preds, lgbm_preds, labels)
    assert w_xgb == 0.5
    assert w_lgbm == 0.5
