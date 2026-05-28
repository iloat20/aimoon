"""Tests for data layer (filtering logic)"""
from __future__ import annotations

import pandas as pd
import pytest

from aimoon.data import filter_by_spot, filter_stock_list


@pytest.fixture
def spot_df() -> pd.DataFrame:
    return pd.DataFrame({
        "stock_code": ["000001", "000002", "600519", "800001"],
        "stock_name": ["Test1", "ST_Test", "Test3", "Test4"],
        "price": [10.0, 50.0, 150.0, 30.0],
        "turnover": [5.0, 10.0, 3.0, 8.0],
        "total_market_cap": [1e10, 5e10, 1e12, 2e10],
        "float_market_cap": [5e9, 3e10, 5e11, 1e10],
    })


class TestFilterBySpot:
    def test_filters_by_market_cap(self, spot_df: pd.DataFrame) -> None:
        result = filter_by_spot(spot_df)
        # 1e12/1e8=10000yi > max 2000 -> filtered out
        assert len(result) == 3

    def test_filters_by_price(self) -> None:
        df = pd.DataFrame({
            "stock_code": ["000001"],
            "stock_name": ["Test"],
            "price": [2.0],  # below min_price=5
            "turnover": [5.0],
            "total_market_cap": [1e10],
            "float_market_cap": [5e9],
        })
        result = filter_by_spot(df)
        assert len(result) == 0


class TestFilterStockList:
    def test_excludes_prefix(self) -> None:
        df = pd.DataFrame({
            "stock_code": ["000001", "800001", "400001"],
            "stock_name": ["Test1", "Test2", "Test3"],
        })
        result = filter_stock_list(df)
        assert len(result) == 1
        assert result.iloc[0]["stock_code"] == "000001"

    def test_excludes_st_name(self) -> None:
        df = pd.DataFrame({
            "stock_code": ["000001", "000002"],
            "stock_name": ["Test", "ST_Something"],
        })
        result = filter_stock_list(df)
        assert len(result) == 1
