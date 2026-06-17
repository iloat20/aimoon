"""Test ML label generation."""
from __future__ import annotations

import numpy as np
import pandas as pd

from aimoon.ml.label_engine import generate_binary_labels, generate_labels, generate_rank_labels


def _make_klines(n_stocks: int = 10, n_days: int = 100) -> dict[str, pd.DataFrame]:
    codes = [f"{i:06d}" for i in range(n_stocks)]
    dates = pd.date_range(end="2026-06-01", periods=n_days, freq="B")
    klines = {}
    for code in codes:
        close = np.random.randn(n_days).cumsum() + 100
        df = pd.DataFrame({"close": close}, index=dates)
        klines[code] = df
    return klines


def test_generate_labels_basic():
    klines = _make_klines(10, 100)
    target = klines["000000"].index[-20]
    labels = generate_labels(klines, target, forward_days=5)
    assert len(labels) > 0
    assert labels.dtype == float


def test_generate_labels_missing_date():
    klines = _make_klines(10, 100)
    future_date = pd.Timestamp("2099-01-01")
    labels = generate_labels(klines, future_date, forward_days=5)
    assert len(labels) == 0


def test_generate_labels_no_future_data():
    klines = _make_klines(10, 100)
    last_date = klines["000000"].index[-1]
    labels = generate_labels(klines, last_date, forward_days=5)
    assert len(labels) == 0


def test_generate_rank_labels():
    klines = _make_klines(20, 100)
    target = klines["000000"].index[-20]
    labels = generate_rank_labels(klines, target, forward_days=5)
    if len(labels) >= 5:
        assert labels.min() >= 0.0
        assert labels.max() <= 1.0


def test_generate_binary_labels():
    klines = _make_klines(20, 100)
    target = klines["000000"].index[-20]
    labels = generate_binary_labels(klines, target, forward_days=5)
    if len(labels) >= 5:
        unique = set(labels.unique())
        assert unique.issubset({0, 1})
