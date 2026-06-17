"""Tests for data.history.fix_kline_dates date repair logic."""

from __future__ import annotations

import numpy as np
import pandas as pd

from aimoon.data.history import fix_kline_dates


class TestFixKlineDates:
    """Tests for the fix_kline_dates function."""

    def should_return_same_dataframe_when_index_is_datetime(self) -> None:
        """DatetimeIndex data should pass through unchanged."""
        dates = pd.date_range("2024-01-01", periods=10, freq="B")
        df = pd.DataFrame({"close": range(10)}, index=dates)
        result = fix_kline_dates(df)
        assert result.index.equals(dates)

    def should_return_none_for_none_input(self) -> None:
        """None input should return None."""
        assert fix_kline_dates(None) is None

    def should_return_empty_for_empty_dataframe(self) -> None:
        """Empty DataFrame should return as-is."""
        df = pd.DataFrame()
        result = fix_kline_dates(df)
        assert result.empty

    def should_use_date_column_when_index_is_integer(self) -> None:
        """Integer index with date column should use the date column."""
        df = pd.DataFrame(
            {"date": ["2024-01-03", "2024-01-04", "2024-01-05"], "close": [10, 11, 12]}
        )
        result = fix_kline_dates(df)
        assert isinstance(result.index, pd.DatetimeIndex)
        assert len(result) == 3

    def should_return_original_when_no_date_column(self) -> None:
        """Integer index without date column should return original."""
        df = pd.DataFrame({"close": [10, 11, 12, 13, 14]})
        result = fix_kline_dates(df)
        assert not isinstance(result.index, pd.DatetimeIndex)
        assert len(result) == 5

    def should_handle_numpy_integer_index(self) -> None:
        """numpy integer index should be detected as integer."""
        df = pd.DataFrame({"close": [10, 11, 12]})
        df.index = np.arange(3, dtype=np.int64)
        result = fix_kline_dates(df)
        assert not isinstance(result.index, pd.DatetimeIndex)
