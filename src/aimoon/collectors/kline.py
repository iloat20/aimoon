"""Historical K-line collector for technical analysis.

Three-tier fallback: akshare stock_zh_a_hist (qfq)
→ akshare stock_zh_a_daily → Tencent fqkline.
Returns ~120 daily bars with OHLCV + pct_change.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

import httpx

from ..models.stock import KlineBar, KlineData
from ..utils import retry_on_connection, silent_failure, to_sina_symbol
from .base import DataCollector

_TENCENT_URL = "https://ifzq.gtimg.cn/appstock/app/fqkline/get"


class KlineCollector(DataCollector[KlineData]):
    """Fetch daily K-line history for a single A-share."""

    name = "kline"

    def __init__(self, days: int = 120) -> None:
        self._days = days

    async def fetch(self, symbol: str, **kwargs: Any) -> KlineData:
        """Fetch K-line with three-level fallback.
        Returns empty bars on total failure.
        """
        # Level 1: stock_zh_a_hist (前复权)
        with silent_failure("kline_akshare_hist"):
            result = await self._fetch_hist(symbol)
            if result and result.bars:
                return result

        # Level 2: stock_zh_a_daily
        with silent_failure("kline_akshare_daily"):
            result = await self._fetch_daily(symbol)
            if result and result.bars:
                return result

        # Level 3: Tencent fqkline
        with silent_failure("kline_tencent_fqkline"):
            result = await self._fetch_tencent(symbol)
            if result and result.bars:
                return result

        return KlineData(symbol=symbol, source="all_failed")

    async def _fetch_hist(self, symbol: str) -> KlineData | None:
        """Fetch via akshare stock_zh_a_hist (前复权)."""
        df = await asyncio.to_thread(self._ak_hist, symbol)
        if df is None or df.empty:
            return None

        bars: list[KlineBar] = []
        for _, row in df.iterrows():
            # Date column can be string or Timestamp
            d = row.get("日期")
            date_str = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
            bars.append(
                KlineBar(
                    date=date_str[:10],
                    open=float(row.get("开盘", 0) or 0),
                    high=float(row.get("最高", 0) or 0),
                    low=float(row.get("最低", 0) or 0),
                    close=float(row.get("收盘", 0) or 0),
                    volume=float(row.get("成交量", 0) or 0),
                    turnover=float(row.get("成交额", 0) or 0),
                    pct_change=float(row.get("涨跌幅", 0) or 0),
                )
            )

        return KlineData(
            symbol=symbol, bars=bars, source="akshare(hist)", period="daily"
        )

    def _ak_hist(self, symbol: str):
        """Run akshare stock_zh_a_hist in a thread (sync API)."""
        import akshare as ak

        start = (datetime.now() - timedelta(days=self._days * 2)).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")
        return retry_on_connection(
            ak.stock_zh_a_hist,
            symbol=symbol,
            period="daily",
            adjust="qfq",
            start_date=start,
            end_date=end,
        )

    async def _fetch_daily(self, symbol: str) -> KlineData | None:
        """Fallback: akshare stock_zh_a_daily (needs sh/sz prefix)."""
        df = await asyncio.to_thread(self._ak_daily, symbol)
        if df is None or df.empty:
            return None

        # Take last N rows
        df = df.tail(self._days)

        bars: list[KlineBar] = []
        for _, row in df.iterrows():
            d = row.get("date")
            date_str = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
            bars.append(
                KlineBar(
                    date=date_str[:10],
                    open=float(row.get("open", 0) or 0),
                    high=float(row.get("high", 0) or 0),
                    low=float(row.get("low", 0) or 0),
                    close=float(row.get("close", 0) or 0),
                    volume=float(row.get("volume", 0) or 0),
                    turnover=float(row.get("amount", 0) or 0),
                    pct_change=float(row.get("pct_chg", 0) or 0),
                )
            )

        return KlineData(
            symbol=symbol, bars=bars, source="akshare(daily)", period="daily"
        )

    def _ak_daily(self, symbol: str):
        """Run akshare stock_zh_a_daily in a thread."""
        import akshare as ak

        prefix = (
            "sh"
            if symbol.startswith("6")
            else ("sz" if symbol.startswith(("0", "3")) else "bj")
        )
        return retry_on_connection(
            ak.stock_zh_a_daily, symbol=f"{prefix}{symbol}", adjust="qfq"
        )

    async def _fetch_tencent(self, symbol: str) -> KlineData | None:
        """Fallback: Tencent fqkline API (前复权).
        Works when push2.eastmoney.com is blocked.
        """
        tsymbol = to_sina_symbol(symbol)
        params = {"param": f"{tsymbol},day,,,{self._days * 2},qfq"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(_TENCENT_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") != 0:
            return None

        day_data = data.get("data", {}).get(tsymbol, {}).get("qfqday", [])
        if not day_data:
            return None

        # Tencent format: [date, open, close, high, low, volume(手)]
        bars: list[KlineBar] = []
        prev_close: float | None = None
        for row in day_data:
            if len(row) < 6:
                continue
            date_str, open_p, close_p, high_p, low_p, vol = row[:6]
            close_f = float(close_p)
            open_f = float(open_p)
            high_f = float(high_p)
            low_f = float(low_p)
            vol_f = float(vol)

            pct = ((close_f - prev_close) / prev_close * 100) if prev_close else 0
            prev_close = close_f

            bars.append(
                KlineBar(
                    date=str(date_str)[:10],
                    open=open_f,
                    high=high_f,
                    low=low_f,
                    close=close_f,
                    volume=vol_f * 100,
                    turnover=0.0,
                    pct_change=round(pct, 2),
                )
            )

        if not bars:
            return None

        # Take last N days
        bars = bars[-self._days :]
        return KlineData(
            symbol=symbol, bars=bars, source="tencent(fqkline)", period="daily"
        )
