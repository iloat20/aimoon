"""Tests for data filters"""
import pandas as pd
import pytest
from aimoon.config import Config
from aimoon.data.filters import filter_universe


@pytest.fixture
def spot_df() -> pd.DataFrame:
    return pd.DataFrame({
        "stock_code": ["000001", "000002", "600519", "800001", "400001"],
        "stock_name": ["Test1", "ST_Test", "Test3", "Test4", "Test5"],
        "price": [10.0, 50.0, 150.0, 30.0, 20.0],
        "turnover": [5.0, 10.0, 3.0, 8.0, 6.0],
        "total_market_cap": [1e10, 5e10, 1e12, 2e10, 1e10],
        "float_market_cap": [5e9, 3e10, 5e11, 1e10, 5e9],
    })


class TestFilterUniverse:
    def test_filters_by_market_cap(self, spot_df: pd.DataFrame) -> None:
        cfg = Config()
        result = filter_universe(spot_df, cfg)
        # 000001 survives; 000002 excluded by ST, 600519 by price+cap, 800001/400001 by prefix
        assert len(result) == 1

    def test_filters_by_price(self) -> None:
        cfg = Config()
        df = pd.DataFrame({
            "stock_code": ["000001"], "stock_name": ["Test"],
            "price": [2.0], "turnover": [5.0],
            "total_market_cap": [1e10], "float_market_cap": [5e9],
        })
        assert len(filter_universe(df, cfg)) == 0

    def test_excludes_st(self, spot_df: pd.DataFrame) -> None:
        cfg = Config()
        result = filter_universe(spot_df, cfg)
        assert not any("ST" in n for n in result["stock_name"].tolist())

    def test_excludes_prefixes(self, spot_df: pd.DataFrame) -> None:
        cfg = Config()
        result = filter_universe(spot_df, cfg)
        assert not any(c.startswith("8") or c.startswith("4") for c in result["stock_code"].tolist())
