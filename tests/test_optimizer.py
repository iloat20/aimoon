"""Tests for optimizer (grid search + walk-forward)."""
import numpy as np
import pandas as pd
import pytest
from aimoon.optimizer import grid_search, walk_forward_validate, OptResult, WalkForwardResult
from aimoon.config import Config
from aimoon.cache import DataCache


@pytest.fixture
def synthetic_klines():
    np.random.seed(42)
    klines = {}
    for code in ["A", "B"]:
        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="B")
        close = 100 * np.exp(np.cumsum(np.random.normal(0.002, 0.02, n)))
        klines[code] = pd.DataFrame({
            "close": close, "high": close * 1.01, "low": close * 0.99,
            "open": close * 1.001,
            "volume": np.random.randint(1000, 10000, n).astype(float),
            "turnover": np.random.uniform(3, 10, n),
            "pct_change": np.concatenate([[0], np.diff(close) / close[:-1] * 100]),
        }, index=dates)
    return klines


class TestGridSearch:
    def test_returns_sorted_results(self, synthetic_klines, tmp_path):
        cfg = Config()
        cache = DataCache(str(tmp_path))
        results = grid_search(
            synthetic_klines, {"A": "A", "B": "B"}, cfg, cache,
            param_ranges={"stop_loss_pct": [0.05, 0.10], "hold_days": [10, 20]},
            metric="sharpe",
        )
        assert len(results) == 4
        assert all(isinstance(r, OptResult) for r in results)
        for i in range(len(results) - 1):
            assert results[i].sharpe >= results[i + 1].sharpe

    def test_max_trials_limits_combos(self, synthetic_klines, tmp_path):
        cfg = Config()
        cache = DataCache(str(tmp_path))
        results = grid_search(
            synthetic_klines, {"A": "A"}, cfg, cache,
            param_ranges={"stop_loss_pct": [0.05, 0.07, 0.10], "hold_days": [10, 15, 20, 25, 30]},
            max_trials=3,
        )
        assert len(results) <= 3

    def test_sortino_metric(self, synthetic_klines, tmp_path):
        cfg = Config()
        cache = DataCache(str(tmp_path))
        results = grid_search(
            synthetic_klines, {"A": "A"}, cfg, cache,
            param_ranges={"stop_loss_pct": [0.05, 0.10]},
            metric="sortino",
        )
        assert len(results) == 2
        assert results[0].sortino >= results[1].sortino


class TestWalkForward:
    def test_basic_walk_forward(self, synthetic_klines, tmp_path):
        cfg = Config()
        cache = DataCache(str(tmp_path))
        wf = walk_forward_validate(
            synthetic_klines, {"A": "A", "B": "B"}, cfg, cache,
            train_pct=0.7, n_splits=2,
        )
        assert isinstance(wf, WalkForwardResult)
        assert len(wf.splits) >= 1
        for s in wf.splits:
            assert s.train_start < s.train_end
            assert s.test_start < s.test_end

    def test_insufficient_data(self, tmp_path):
        cfg = Config()
        cache = DataCache(str(tmp_path))
        short_klines = {"A": pd.DataFrame({
            "close": [10.0] * 50, "high": [10.1] * 50, "low": [9.9] * 50,
            "open": [10.0] * 50, "volume": [1000.0] * 50,
            "turnover": [5.0] * 50, "pct_change": [0.0] * 50,
        }, index=pd.date_range("2024-01-01", periods=50, freq="B"))}
        wf = walk_forward_validate(short_klines, {"A": "A"}, cfg, cache)
        assert len(wf.splits) == 0
