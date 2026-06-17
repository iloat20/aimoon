"""Custom data provider that wraps aimoon kline data for QF-Lib.

Converts pandas DataFrames (with open/high/low/close/volume columns)
into QF-Lib-compatible price bar data.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import pandas as pd

from aimoon.qf_backtest.imports import QF_AVAILABLE

if QF_AVAILABLE:
    from qf_lib.common.enums.price_field import PriceField
    from qf_lib.common.enums.security_type import SecurityType
    from qf_lib.common.tickers.tickers import Ticker
    from qf_lib.containers.dataframe.qf_dataframe import QFDataFrame
    from qf_lib.containers.series.qf_series import QFSeries
    from qf_lib.data_providers.abstract_price_data_provider import (
        AbstractPriceDataProvider,
    )

logger = logging.getLogger(__name__)


_COLUMN_MAP = {
    "open": PriceField.Open,
    "high": PriceField.High,
    "low": PriceField.Low,
    "close": PriceField.Close,
    "volume": PriceField.Volume,
}


class SimpleTicker(Ticker):
    """Minimal ticker implementation wrapping a stock code string."""

    point_value = 1.0

    def __init__(self, code: str) -> None:
        self._code = code
        self.ticker = code
        if QF_AVAILABLE:
            self.security_type = SecurityType.STOCK
        else:
            self.security_type = "STK"

    @classmethod
    def from_string(cls, code: str) -> SimpleTicker:
        return cls(code)

    @property
    def as_string(self) -> str:
        return self._code

    def __str__(self) -> str:
        return self._code

    def __repr__(self) -> str:
        return f"SimpleTicker({self._code})"

    def __hash__(self) -> int:
        return hash(self._code)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SimpleTicker):
            return self._code == other._code
        if isinstance(other, str):
            return self._code == other
        return NotImplemented


class AimoonDataProvider(AbstractPriceDataProvider if QF_AVAILABLE else object):  # type: ignore[misc]
    """Custom data provider that serves pre-computed kline data to QF-Lib.

    Reads from a dict of {code: pd.DataFrame} with columns
    [open, high, low, close, volume] and a datetime index.
    """

    def __init__(self, klines: dict[str, pd.DataFrame]) -> None:
        if not QF_AVAILABLE:
            raise ImportError("qf-lib is required but not installed")
        super().__init__()
        self._klines: dict[str, pd.DataFrame] = klines
        self._ticker_map: dict[str, SimpleTicker] = {}

    def _get_ticker(self, code: str) -> SimpleTicker:
        if code not in self._ticker_map:
            self._ticker_map[code] = SimpleTicker(code)
        return self._ticker_map[code]

    def supported_ticker_types(self) -> set[type]:
        return {SimpleTicker}

    def price_field_to_str_map(self) -> dict[PriceField, str]:
        return {
            PriceField.Open: "open",
            PriceField.High: "high",
            PriceField.Low: "low",
            PriceField.Close: "close",
            PriceField.Volume: "volume",
        }

    def get_history(
        self,
        tickers: Any,
        fields: Any,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        frequency: Any = None,
        look_ahead_bias: bool = False,
        **kwargs: Any,
    ) -> Any:
        was_single_ticker = isinstance(tickers, SimpleTicker)
        ticker_list = [tickers] if was_single_ticker else list(tickers)
        field_list = [fields] if not isinstance(fields, (list, tuple)) else list(fields)
        n_fields = len(field_list)

        if n_fields == 1:
            if was_single_ticker:
                return self._get_series(ticker_list[0], field_list[0], start_date, end_date)
            return self._get_multi_ticker_series(ticker_list, field_list[0], start_date, end_date)
        if was_single_ticker:
            return self._get_dataframe(ticker_list[0], field_list, start_date, end_date)
        fallback = self._get_multi_ticker_series(ticker_list, field_list[0], start_date, end_date)
        return fallback if field_list else QFSeries(dtype=float)

    def get_price(
        self,
        tickers: Any,
        fields: Any,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        frequency: Any = None,
        look_ahead_bias: bool = False,
    ) -> Any:
        return self.get_history(tickers, fields, start_date, end_date, frequency, look_ahead_bias)

    def get_last_available_price(
        self,
        tickers: Any,
        frequency: Any = None,
        end_time: datetime | None = None,
    ) -> Any:
        is_single = isinstance(tickers, SimpleTicker)
        ticker_list = [tickers] if is_single else list(tickers)
        data: dict[SimpleTicker, float] = {}
        for t in ticker_list:
            df = self._klines.get(t.as_string)
            if df is not None and len(df) > 0:
                data[t] = float(df["close"].iloc[-1])
        if is_single:
            return next(iter(data.values()), 0.0)
        series = pd.Series(data, dtype=float)
        series.index.name = None
        return QFSeries(series) if QF_AVAILABLE else series

    def historical_price(
        self,
        tickers: Any,
        fields: Any,
        nr_of_bars: int,
        end_date: datetime | None = None,
        frequency: Any = None,
    ) -> Any:
        ticker_list = [tickers] if isinstance(tickers, SimpleTicker) else list(tickers)
        field_list = [fields] if not isinstance(fields, (list, tuple)) else list(fields)
        n_tickers = len(ticker_list)
        n_fields = len(field_list)

        if n_tickers == 1 and n_fields == 1:
            df = self._klines.get(ticker_list[0].as_string)
            if df is None or len(df) == 0:
                return QFSeries(dtype=float)
            series = df["close"].iloc[-nr_of_bars:]
            series.index = series.index.tz_localize(None)
            return QFSeries(series)
        elif n_tickers == 1:
            df = self._klines.get(ticker_list[0].as_string)
            if df is None or len(df) == 0:
                return QFDataFrame()
            subset = df.iloc[-nr_of_bars:]
            subset.index = subset.index.tz_localize(None)
            return QFDataFrame(subset)
        else:
            arrays: dict[str, dict[str, Any]] = {}
            for t in ticker_list:
                df = self._klines.get(t.as_string)
                if df is not None:
                    arrays[t.as_string] = {
                        f.name: df[f.name.lower()].iloc[-nr_of_bars:].tolist() for f in field_list
                    }
            return arrays

    def _ensure_datetime_index(self, df: pd.DataFrame) -> pd.DataFrame:
        if not isinstance(df.index, pd.DatetimeIndex):
            try:
                df = df.copy()
                df.index = pd.to_datetime(df.index)
            except Exception:
                pass
        return df

    def _filter_date(
        self, series: pd.Series, start_date: datetime | None, end_date: datetime | None
    ) -> pd.Series:
        idx = series.index
        if not isinstance(idx, pd.DatetimeIndex):
            return series
        mask = pd.Series(True, index=idx)
        if start_date:
            try:
                ts = pd.Timestamp(start_date)
                mask &= idx >= ts
            except (ValueError, TypeError):
                pass
        if end_date:
            try:
                ts = pd.Timestamp(end_date)
                mask &= idx <= ts
            except (ValueError, TypeError):
                pass
        return series[mask] if not mask.all() else series

    def _get_series(
        self,
        ticker: SimpleTicker,
        field: Any,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> Any:
        from qf_lib.containers.series.qf_series import QFSeries

        df = self._klines.get(ticker.as_string)
        if df is None or df.empty:
            return QFSeries(dtype=float)
        col = self._resolve_field(field)
        if col not in df.columns:
            return QFSeries(dtype=float)
        df = self._ensure_datetime_index(df)
        series = self._filter_date(df[col].copy(), start_date, end_date)
        series.index = pd.to_datetime(series.index).tz_localize(None)
        return QFSeries(series)

    def _get_dataframe(
        self,
        ticker: SimpleTicker,
        fields: list[Any],
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> Any:
        from qf_lib.containers.dataframe.qf_dataframe import QFDataFrame

        df = self._klines.get(ticker.as_string)
        if df is None or df.empty:
            return QFDataFrame()
        cols = [self._resolve_field(f) for f in fields if self._resolve_field(f) in df.columns]
        df = self._ensure_datetime_index(df)
        subset = df[cols].copy()
        subset = self._filter_date(subset, start_date, end_date)
        subset.index = pd.to_datetime(subset.index).tz_localize(None)
        return QFDataFrame(subset)

    def _get_multi_ticker_series(
        self,
        tickers: list[SimpleTicker],
        field: Any,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> Any:
        col = self._resolve_field(field)
        data: dict[SimpleTicker, float] = {}
        for t in tickers:
            df = self._klines.get(t.as_string)
            if df is None or df.empty or col not in df.columns:
                data[t] = float("nan")
                continue
            df = self._ensure_datetime_index(df)
            series = self._filter_date(df[col], start_date, end_date)
            if not series.empty:
                data[t] = float(series.iloc[0])
            else:
                data[t] = float("nan")
        from qf_lib.containers.series.qf_series import QFSeries

        return QFSeries(data, dtype=float)

    def _resolve_field(self, field: Any) -> str:
        if isinstance(field, PriceField):
            return self.price_field_to_str_map().get(field, "close")
        if isinstance(field, str):
            return field.lower()
        return "close"
