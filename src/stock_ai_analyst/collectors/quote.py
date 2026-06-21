"""Real-time stock quote collector with three-level fallback.

Priority: akshare → 新浪API → 腾讯API
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Optional

import httpx

from ..models.stock import StockQuote


# Sina API endpoints
_SINA_URL = "http://hq.sinajs.cn/list={symbol}"
# Tencent API endpoints
_TENCENT_URL = "http://qt.gtimg.cn/q={symbol}"

# Sina referer header (required)
_SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


def _sina_symbol(symbol: str) -> str:
    """Convert 6-digit code to Sina format: sh600519 or sz000001."""
    if symbol.startswith("6"):
        return f"sh{symbol}"
    elif symbol.startswith(("0", "3")):
        return f"sz{symbol}"
    elif symbol.startswith(("4", "8")):
        return f"bj{symbol}"
    return f"sz{symbol}"


def _tencent_symbol(symbol: str) -> str:
    """Convert 6-digit code to Tencent format: sh600519 or sz000001."""
    return _sina_symbol(symbol)


class QuoteCollector:
    """Fetch real-time stock quotes with multi-source fallback."""

    def __init__(self, client: Optional[httpx.AsyncClient] = None) -> None:
        self._client = client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def fetch(self, symbol: str, name: str = "") -> StockQuote:
        """Fetch quote with three-level fallback."""
        # Level 1: akshare
        try:
            result = await self._fetch_akshare(symbol)
            if result and result.price > 0:
                result.name = name or result.name
                return result
        except Exception:
            pass

        # Level 2: Sina
        try:
            result = await self._fetch_sina(symbol)
            if result and result.price > 0:
                result.name = name or result.name
                return result
        except Exception:
            pass

        # Level 3: Tencent
        try:
            result = await self._fetch_tencent(symbol)
            if result and result.price > 0:
                result.name = name or result.name
                return result
        except Exception:
            pass

        # All failed
        return StockQuote(
            symbol=symbol,
            name=name,
            source="all_failed",
            updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

    async def _fetch_akshare(self, symbol: str) -> Optional[StockQuote]:
        """Fetch via akshare (most reliable for A-shares)."""
        try:
            import akshare as ak
            import pandas as pd

            df = ak.stock_zh_a_spot_em()
            row = df[df["代码"] == symbol]
            if row.empty:
                return None

            r = row.iloc[0]
            return StockQuote(
                symbol=symbol,
                name=str(r.get("名称", "")),
                price=float(r.get("最新价", 0)),
                change=float(r.get("涨跌额", 0)),
                change_pct=float(r.get("涨跌幅", 0)),
                volume=int(r.get("成交量", 0)),
                amount=float(r.get("成交额", 0)),
                high=float(r.get("最高", 0)),
                low=float(r.get("最低", 0)),
                open=float(r.get("今开", 0)),
                prev_close=float(r.get("昨收", 0)),
                turnover=float(r.get("换手率", 0)),
                pe=float(r.get("市盈率-动态", 0) or 0),
                source="akshare",
                updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
        except Exception as e:
            raise RuntimeError(f"akshare fetch failed: {e}")

    async def _fetch_sina(self, symbol: str) -> Optional[StockQuote]:
        """Fetch via Sina finance API (backup 1).
        
        Sina format fields:
        0:name, 1:open, 2:prev_close, 3:price, 4:high, 5:low,
        6:bid, 7:ask, 8:volume(shares), 9:amount(yuan),
        10-19: buy5, 20-29: sell5, 30:date, 31:time
        """
        client = await self._get_client()
        url = _SINA_URL.format(symbol=_sina_symbol(symbol))
        resp = await client.get(url, headers=_SINA_HEADERS)
        resp.raise_for_status()

        text = resp.text
        if not text or "FAILED" in text or text.count(",") < 30:
            return None

        match = re.search(r'"([^"]+)"', text)
        if not match:
            return None
        fields = match.group(1).split(",")
        if len(fields) < 32:
            return None

        open_price = float(fields[1]) if fields[1] else 0
        prev_close = float(fields[2]) if fields[2] else 0
        price = float(fields[3]) if fields[3] else 0

        change = price - prev_close
        change_pct = (change / prev_close * 100) if prev_close else 0

        return StockQuote(
            symbol=symbol,
            name=fields[0],
            price=round(price, 2),
            change=round(change, 2),
            change_pct=round(change_pct, 2),
            volume=int(float(fields[8])) if fields[8] else 0,
            amount=float(fields[9]) if fields[9] else 0,
            high=float(fields[4]) if fields[4] else 0,
            low=float(fields[5]) if fields[5] else 0,
            open=round(open_price, 2),
            prev_close=round(prev_close, 2),
            source="新浪财经",
            updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

    async def _fetch_tencent(self, symbol: str) -> Optional[StockQuote]:
        """Fetch via Tencent finance API (backup 2).
        
        Tencent format fields (~ delimited):
        0:market, 1:name, 2:code, 3:price, 4:prev_close, 5:open,
        6:volume(手), 7:buy_vol, 8:sell_vol,
        9-18: buy5, 19-28: sell5,
        29:?, 30:?, 31:datetime, 32:change, 33:change_pct,
        34:high, 35:low, 36:price/vol/amount,
        37:volume, 38:amount(万), 39:turnover%, 40:PE
        """
        client = await self._get_client()
        url = _TENCENT_URL.format(symbol=_tencent_symbol(symbol))
        resp = await client.get(url)
        resp.raise_for_status()

        text = resp.text
        if not text or text.count("~") < 30:
            return None

        match = re.search(r'="([^"]+)"', text)
        if not match:
            return None
        fields = match.group(1).split("~")
        if len(fields) < 40:
            return None

        return StockQuote(
            symbol=symbol,
            name=fields[1],
            price=float(fields[3]) if fields[3] else 0,
            change=float(fields[32]) if fields[32] else 0,
            change_pct=float(fields[33]) if fields[33] else 0,
            volume=int(float(fields[6])) if fields[6] else 0,
            amount=float(fields[38]) * 10000 if fields[38] else 0,  # 万元→元
            high=float(fields[34]) if fields[34] else 0,
            low=float(fields[35]) if fields[35] else 0,
            open=float(fields[5]) if fields[5] else 0,
            prev_close=float(fields[4]) if fields[4] else 0,
            turnover=float(fields[39]) if fields[39] else 0,
            pe=float(fields[40]) if len(fields) > 40 and fields[40] else 0,
            source="腾讯行情",
            updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
