"""Tests for cache provider"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from aimoon.cache.provider import DataCache


@pytest.fixture
def sample_df() -> pd.DataFrame:
    return pd.DataFrame({
        "close": [10.0, 11.0, 12.0],
        "open": [9.5, 10.5, 11.5],
        "volume": [1000, 2000, 3000],
    }, index=pd.date_range("2025-01-01", periods=3))


@pytest.fixture
def cache(tmp_path) -> DataCache:
    return DataCache(cache_dir=str(tmp_path / "test_cache"), ttl_hours=1)


class TestDataCache:
    def test_put_and_get(self, cache: DataCache, sample_df: pd.DataFrame) -> None:
        cache.put("000001", sample_df)
        result = cache.get("000001")
        assert result is not None
        assert len(result) == 3
        assert list(result.columns) == ["close", "open", "volume"]

    def test_get_missing_returns_none(self, cache: DataCache) -> None:
        assert cache.get("nonexistent") is None

    def test_expired_returns_none(self, tmp_path, sample_df: pd.DataFrame) -> None:
        cache = DataCache(cache_dir=str(tmp_path / "exp_cache"), ttl_hours=0)
        cache.put("000001", sample_df)
        time.sleep(0.1)
        assert cache.get("000001") is None

    def test_clear(self, cache: DataCache, sample_df: pd.DataFrame) -> None:
        cache.put("000001", sample_df)
        cache.put("600519", sample_df)
        removed = cache.clear()
        assert removed == 2
        assert cache.get("000001") is None
        assert cache.get("600519") is None
