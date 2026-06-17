"""Tushare data source -- supplementary data provider.

Provides high-quality A-share data via tushare.pro API:
- Daily OHLCV (more reliable than eastmoney API)
- Dividend data
- Financial indicators (ROE, PE, PB, etc.)
- Index constituents

Usage:
    from aimoon.data.tushare_source import TushareSource
    ts = TushareSource("your_token")
    df = ts.get_daily("000001.SZ", start="20240101", end="20241231")
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from aimoon.result import Err, Ok, Result

logger = logging.getLogger(__name__)

try:
    import tushare as ts

    _HAS_TUSHARE = True
except ImportError:
    _HAS_TUSHARE = False

_DEFAULT_TOKEN = ""  # Set via config or environment


class TushareSource:
    """Tushare.pro data provider.

    Requires a tushare.pro API token (free tier available at tushare.pro).
    Register and get token at: https://tushare.pro
    """

    def __init__(self, token: str = _DEFAULT_TOKEN) -> None:
        self._token = token
        self._api = None

    @property
    def available(self) -> bool:
        return _HAS_TUSHARE and bool(self._token)

    def _get_api(self):
        if self._api is None and self.available:
            self._api = ts.set_token(self._token)
            self._api = ts.pro_api()
        return self._api

    def get_daily(
        self,
        code: str,
        start: str | None = None,
        end: str | None = None,
    ) -> Result[pd.DataFrame, str]:
        """Get daily OHLCV data.

        Parameters
        ----------
        code : str
            Stock code with suffix, e.g. "000001.SZ" or "600519.SH".
        start : str, optional
            Start date YYYYMMDD. Default: 2 years ago.
        end : str, optional
            End date YYYYMMDD. Default: today.

        Returns
        -------
        Result[pd.DataFrame, str]
            DataFrame with columns: date, open, high, low, close, volume, amount.
        """
        if not self.available:
            return Err("tushare not installed or token not set")

        if not start:
            start = (date.today() - timedelta(days=730)).strftime("%Y%m%d")
        if not end:
            end = date.today().strftime("%Y%m%d")

        try:
            api = self._get_api()
            df = api.daily(ts_code=code, start_date=start, end_date=end)
            if df is None or df.empty:
                return Err(f"{code}: no data from tushare")

            df = df.rename(
                columns={
                    "trade_date": "date",
                    "vol": "volume",
                    "pct_chg": "pct_change",
                }
            )
            df = df.sort_values("date")
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")

            # Ensure standard columns exist
            for col in ("open", "high", "low", "close", "volume", "amount"):
                if col not in df.columns:
                    df[col] = 0.0

            return Ok(df)
        except Exception as e:
            return Err(f"{code}: tushare error: {e}")

    def get_fina_indicator(
        self,
        code: str,
    ) -> Result[pd.DataFrame, str]:
        """Get financial indicators (ROE, PE, PB, etc.).

        Parameters
        ----------
        code : str
            Stock code with suffix, e.g. "000001.SZ".

        Returns
        -------
        Result[pd.DataFrame, str]
            DataFrame with financial indicators per reporting period.
        """
        if not self.available:
            return Err("tushare not installed or token not set")

        try:
            api = self._get_api()
            df = api.fina_indicator(ts_code=code)
            if df is None or df.empty:
                return Err(f"{code}: no fina_indicator data")

            df = df.sort_values("end_date")
            return Ok(df)
        except Exception as e:
            return Err(f"{code}: tushare fina_indicator error: {e}")

    def get_daily_basic(
        self,
        code: str,
        fields: str = "ts_code,trade_date,close,turnover_rate,turnover_rate_f,volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,total_share,float_share,free_share,total_mv,circ_mv",  # noqa: E501,
    ) -> Result[pd.DataFrame, str]:
        """Get daily basic data (PE, PB, PS, market cap, etc.).

        Parameters
        ----------
        code : str
            Stock code with suffix.
        fields : str
            Comma-separated field names.

        Returns
        -------
        Result[pd.DataFrame, str]
        """
        if not self.available:
            return Err("tushare not installed or token not set")

        try:
            api = self._get_api()
            df = api.daily_basic(ts_code=code, fields=fields)
            if df is None or df.empty:
                return Err(f"{code}: no daily_basic data")

            df = df.rename(columns={"trade_date": "date"})
            df = df.sort_values("date")
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")
            return Ok(df)
        except Exception as e:
            return Err(f"{code}: tushare daily_basic error: {e}")

    def get_index_weight(
        self,
        index_code: str = "399300.SZ",
    ) -> Result[pd.DataFrame, str]:
        """Get index constituent weights.

        Parameters
        ----------
        index_code : str
            Index code, e.g. "399300.SZ" (CSI 300).

        Returns
        -------
        Result[pd.DataFrame, str]
            DataFrame with columns: code, weight.
        """
        if not self.available:
            return Err("tushare not installed or token not set")

        try:
            api = self._get_api()
            df = api.index_weight(index_code=index_code)
            if df is None or df.empty:
                return Err(f"{index_code}: no index weight data")

            df = df.sort_values("trade_date")
            return Ok(df)
        except Exception as e:
            return Err(f"{index_code}: tushare index_weight error: {e}")

    @staticmethod
    def is_available() -> bool:
        """Check if tushare is installed and configured."""
        return _HAS_TUSHARE and bool(_DEFAULT_TOKEN)
