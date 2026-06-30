"""Real-time stock quote collector with dual-source fallback (Sina + Tencent)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from aimoon.adapters.driven.common.cache import DiskTtlCache
from aimoon.core.domain.entities.quote import StockQuote
from aimoon.core.domain.services.symbols import to_sina_symbol

from .base import DataCollector

# 1-minute disk TTL cache — repeated runs during debug skip HTTP requests entirely.
_quote_cache = DiskTtlCache(namespace="quote", ttl_seconds=60)

# Sina API endpoints
_SINA_URL = "https://hq.sinajs.cn/list={symbol}"
# Tencent API endpoints
_TENCENT_URL = "https://qt.gtimg.cn/q={symbol}"

# Sina referer header (required)
_SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
}


class QuoteCollector(DataCollector[StockQuote]):
    """Fetch real-time stock quotes with multi-source fallback."""

    name = "quote"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client_provided = client is not None
        self._client = client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client_provided:
            await self._client.aclose()
            self._client = None

    async def fetch(self, symbol: str, **kwargs: Any) -> StockQuote:
        """Fetch quote with caching. Cache hit avoids HTTP requests entirely."""
        cached = _quote_cache.get(f"quote:{symbol}")
        if cached is not None:
            return StockQuote.model_validate(cached)

        result = await self._fetch_uncached(symbol, **kwargs)
        if result and result.price > 0:
            _quote_cache.set(f"quote:{symbol}", result.model_dump())
        return result

    async def _fetch_uncached(self, symbol: str, **kwargs: Any) -> StockQuote:
        """原始 fetch 逻辑（无缓存）。"""
        name = kwargs.pop("name", "")
        # Level 1: Sina API (<1s)
        try:
            result = await self._fetch_sina(symbol)
            if result and result.price > 0:
                if name and (not result.name or result.name == symbol):
                    result.name = name
                if result.pe <= 0:
                    await self._enrich_from_tencent(symbol, result)
                return result
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            pass
        # Level 2: Tencent API
        result = await self._fetch_tencent(symbol, name)
        if result is not None and result.price > 0:
            if name and (not result.name or result.name == symbol):
                result.name = name
            return result
        # All sources failed
        return StockQuote(symbol=symbol, name=name, source="all_failed")

    async def _enrich_from_tencent(self, symbol: str, quote: StockQuote) -> None:
        """Enrich quote with PE/PB/market_cap from Tencent API (best-effort)."""
        try:
            tc = await self._fetch_tencent(symbol, "")
            if tc and tc.price > 0:
                if quote.pe <= 0:
                    quote.pe = tc.pe
                if quote.pb <= 0:
                    quote.pb = tc.pb
                if quote.market_cap <= 0:
                    quote.market_cap = tc.market_cap
                if quote.turnover <= 0:
                    quote.turnover = tc.turnover
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            pass

    async def _fetch_sina(self, symbol: str) -> StockQuote | None:
        """Fetch from Sina API."""
        from aimoon.adapters.driven.config.settings import get_settings

        tsymbol = to_sina_symbol(symbol)
        url = _SINA_URL.format(symbol=tsymbol)
        headers = {
            **_SINA_HEADERS,
            "User-Agent": get_settings().default_user_agent,
        }
        client = await self._get_client()
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        text = resp.text
        if not text or "=" not in text:
            return None

        quote_str = text.split("=")[1].strip('"')
        if not quote_str:
            return None

        parts = quote_str.split(",")
        if len(parts) < 32:
            return None

        name = parts[0]
        open_price = float(parts[1])
        prev_close = float(parts[2])
        price = float(parts[3])
        high = float(parts[4])
        low = float(parts[5])
        volume = int(float(parts[8]))
        amount = float(parts[9])

        change = price - prev_close
        change_pct = (change / prev_close) * 100 if prev_close != 0 else 0

        return StockQuote(
            symbol=symbol,
            name=name,
            price=price,
            change=round(change, 2),
            change_pct=round(change_pct, 2),
            volume=volume,
            amount=amount,
            high=high,
            low=low,
            open=open_price,
            prev_close=prev_close,
            turnover=0.0,  # 新浪 API 不提供换手率（需要流通股本）
            pe=-1.0,  # 新浪 API 不提供 PE
            source="新浪",
            updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

    async def _fetch_tencent(self, symbol: str, name: str = "") -> StockQuote | None:
        """Fetch from Tencent API (qt.gtimg.cn). Returns None on parse failure.
        tsymbol = to_sina_symbol(symbol)
        Caller must handle None result.
        """
        tsymbol = to_sina_symbol(symbol)
        url = _TENCENT_URL.format(symbol=tsymbol)
        client = await self._get_client()
        resp = await client.get(url)
        resp.raise_for_status()
        text = resp.text
        if not text or "=" not in text:
            return None
        quote_str = text.split("=")[1].strip('"')
        parts = quote_str.split("~")
        if len(parts) < 40:
            return None
        # Prefer API name over passed-in name (API returns real stock name)
        api_name = parts[1] if parts[1] else name
        if api_name:
            name = api_name
        price = float(parts[3])
        prev_close = float(parts[4])
        change = price - prev_close
        change_pct = (change / prev_close) * 100 if prev_close != 0 else 0
        volume = int(float(parts[6]))
        amount = float(parts[37]) * 10000 if parts[37] else 0
        high = float(parts[33])
        low = float(parts[34])
        open_price = float(parts[5])
        turnover = float(parts[38]) if parts[38] else 0
        pe = float(parts[39]) if len(parts) > 39 and parts[39] else 0.0
        pb = float(parts[46]) if len(parts) > 46 and parts[46] else 0.0
        market_cap = float(parts[45]) * 1e8 if len(parts) > 45 and parts[45] else 0.0
        return StockQuote(
            symbol=symbol,
            name=name,
            price=price,
            change=round(change, 2),
            change_pct=round(change_pct, 2),
            volume=volume,
            amount=amount,
            high=high,
            low=low,
            open=open_price,
            prev_close=prev_close,
            turnover=turnover,
            pe=pe,
            pb=pb,
            market_cap=market_cap,
            source="腾讯",
            updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
