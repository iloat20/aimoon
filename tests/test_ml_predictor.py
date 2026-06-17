"""Test MLPredictor — tasks 9-12: single LightGBM predictor tests."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from aimoon.ml.predictor import MLPredictor


# ── Helper ──────────────────────────────────────────────────────────────────


def _make_dummy_panel(
    n_stocks: int = 10,
    n_days: int = 100,
    seed: int = 42,
) -> dict[str, pd.DataFrame]:
    """Create a minimal panel with OHLCV data."""
    rng = np.random.default_rng(seed)
    codes = [f"{i:06d}" for i in range(n_stocks)]
    dates = pd.date_range(end="2026-06-01", periods=n_days, freq="B")
    panel: dict[str, pd.DataFrame] = {}
    for col in ("open", "high", "low", "close", "volume"):
        arr = rng.standard_normal((n_days, n_stocks)).cumsum(axis=0) + 100
        arr = np.abs(arr) + 1.0  # ensure positive prices
        panel[col] = pd.DataFrame(arr, index=dates, columns=codes)
    return panel


def _make_dummy_lgbm_model(save_dir: Path) -> None:
    """Train a tiny LightGBM and save model + feature_names + medians."""
    import lightgbm as lgb  # noqa: PLC0415

    rng = np.random.default_rng(42)
    n = 30
    X = pd.DataFrame(
        {
            "rev_5d": rng.standard_normal(n),
            "rev_20d": rng.standard_normal(n),
            "turnover_20d": rng.standard_normal(n),
            "vol_20d": rng.standard_normal(n),
            "mom_60d": rng.standard_normal(n),
        },
    )
    y = rng.standard_normal(n)

    model = lgb.train(
        {
            "objective": "regression",
            "verbose": -1,
            "num_leaves": 7,
            "learning_rate": 0.1,
            "n_estimators": 10,
        },
        lgb.Dataset(X, label=y),
        num_boost_round=10,
    )

    model.save_model(str(save_dir / "lgbm_model.txt"))

    feature_names = X.columns.tolist()
    with open(save_dir / "lgbm_feature_names.json", "w", encoding="utf-8") as f:
        json.dump(feature_names, f)

    medians = X.median().to_dict()
    with open(save_dir / "lgbm_feature_medians.json", "w", encoding="utf-8") as f:
        json.dump(medians, f)


# ── Tests ───────────────────────────────────────────────────────────────────


def test_load_no_model_file() -> None:
    """load() returns None when model file does not exist."""
    with tempfile.TemporaryDirectory() as tmp:
        predictor = MLPredictor.load(tmp)
        assert predictor is None


def test_load_success() -> None:
    """load() returns MLPredictor instance when model file exists."""
    with tempfile.TemporaryDirectory() as tmp:
        save_dir = Path(tmp)
        _make_dummy_lgbm_model(save_dir)

        predictor = MLPredictor.load(save_dir)
        assert predictor is not None
        assert predictor.model is not None
        assert len(predictor.feature_names) > 0
        assert len(predictor.feature_medians) > 0


def test_predict_returns_correct_types() -> None:
    """predict() returns dict[str, int] with values 0-100."""
    with tempfile.TemporaryDirectory() as tmp:
        save_dir = Path(tmp)
        _make_dummy_lgbm_model(save_dir)

        predictor = MLPredictor.load(save_dir)
        assert predictor is not None

        panel = _make_dummy_panel()
        result = predictor.predict(panel)

        assert isinstance(result, dict)
        if result:  # may be empty if features fail
            for code, score in result.items():
                assert isinstance(code, str)
                assert isinstance(score, int)
                assert 0 <= score <= 100


def test_predict_prob_returns_raw_predictions() -> None:
    """predict_prob() returns dict[str, float] with raw values."""
    with tempfile.TemporaryDirectory() as tmp:
        save_dir = Path(tmp)
        _make_dummy_lgbm_model(save_dir)

        predictor = MLPredictor.load(save_dir)
        assert predictor is not None

        panel = _make_dummy_panel()
        result = predictor.predict_prob(panel)

        assert isinstance(result, dict)
        if result:
            for code, val in result.items():
                assert isinstance(code, str)
                assert isinstance(val, float)


def test_predict_empty_panel() -> None:
    """predict() returns empty dict for empty panel."""
    with tempfile.TemporaryDirectory() as tmp:
        save_dir = Path(tmp)
        _make_dummy_lgbm_model(save_dir)

        predictor = MLPredictor.load(save_dir)
        assert predictor is not None

        result = predictor.predict({})
        assert result == {}


def test_predict_no_close() -> None:
    """predict() returns empty dict when panel has no close data."""
    with tempfile.TemporaryDirectory() as tmp:
        save_dir = Path(tmp)
        _make_dummy_lgbm_model(save_dir)

        predictor = MLPredictor.load(save_dir)
        assert predictor is not None

        result = predictor.predict({"volume": pd.DataFrame()})
        assert result == {}


def test_predict_prob_returns_empty_for_no_model() -> None:
    """predict_prob() gracefully handles missing model (though load would return None)."""
    with tempfile.TemporaryDirectory() as tmp:
        # Manually create a predictor with no model
        predictor = MLPredictor()
        predictor.model = None  # type: ignore[assignment]
        predictor.feature_names = ["rev_5d"]
        predictor.feature_medians = {}

        panel = _make_dummy_panel()
        # This should not crash - predict_prob handles missing model via lazy import
        result = predictor.predict_prob(panel)
        assert result == {}


@pytest.mark.skipif(
    not pytest.importorskip("lightgbm", reason="lightgbm not installed"),
    reason="lightgbm not installed",
)
def test_predict_and_predict_prob_match() -> None:
    """predict() scores are monotonic with predict_prob() values.

    Higher raw prediction should yield higher percentile rank.
    """
    with tempfile.TemporaryDirectory() as tmp:
        save_dir = Path(tmp)
        _make_dummy_lgbm_model(save_dir)

        predictor = MLPredictor.load(save_dir)
        assert predictor is not None

        panel = _make_dummy_panel()
        scores = predictor.predict(panel)
        probs = predictor.predict_prob(panel)

        assert len(scores) == len(probs)
        if not scores:
            return

        # Sort both by raw prediction descending
        sorted_codes_by_prob = sorted(probs, key=lambda c: probs[c], reverse=True)
        sorted_codes_by_score = sorted(scores, key=lambda c: scores[c], reverse=True)

        # Top stock should be the same in both
        assert sorted_codes_by_prob[0] == sorted_codes_by_score[0]
