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
    _default_timeout = 10.0

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(client)

    async def fetch(self, symbol: str, **kwargs: Any) -> StockQuote:
        """Fetch quote with caching. Cache hit avoids HTTP requests entirely."""
        cached = await _quote_cache.aget(f"quote:{symbol}")
        if cached is not None:
            return StockQuote.model_validate(cached)

        result = await self._fetch_uncached(symbol, **kwargs)
        if result and result.price > 0:
            await _quote_cache.aset(f"quote:{symbol}", result.model_dump())
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
                # 新浪恒不提供换手率(turnover), 且 PE/PB/市值可能缺失;
                # 任一缺失字段即补腾讯真实值(_enrich_from_tencent 按字段独立守卫, 不覆盖已有真值)。
                # 否则 turnover 恒为 0 会触发完整性检查误报"量换不一致"(第七轮 W3)。
                if (
                    result.pe <= 0
                    or result.pb <= 0
                    or result.market_cap <= 0
                    or result.turnover <= 0
                ):
                    await self._enrich_from_tencent(symbol, result)
                return result
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            pass
        # Level 2: Tencent API
        # 契约: 单源失败永不 abort。L2 同样是网络调用, 必须包异常
        # (腾讯 5xx / 超时 / 字段空致 float() 抛错), 否则会冒泡出 fetch()→orchestrate()
        # 拖垮整条 pipeline (原缺此保护, 2026-07-14 修复)。
        try:
            result = await self._fetch_tencent(symbol, name)
            if result is not None and result.price > 0:
                if name and (not result.name or result.name == symbol):
                    result.name = name
                return result
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            pass
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
        # 新浪 parts[8] = 成交量(股); 模板与腾讯/K线 canon 均以"手"为单位,
        # 故 ÷100 归一化为手(2026-07-09 实抓双源核对: 新浪 3409634 股 = 腾讯 34096 手)。
        volume = int(float(parts[8]) / 100)
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
            pe=0.0,  # 新浪 API 不提供 PE(哨兵 0,下面 _enrich_from_tencent 补真实值)
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
        # 腾讯 qt.gtimg.cn (~分隔) 字段布局 —— 2026-07-14 实抓 sh600519 核对确认:
        #   [6]=成交量(手)  [37]=成交额(万元)  [38]=换手率(%)  [39]=市盈率(TTM)
        #   [44]=流通市值(亿)  [45]=总市值(亿)  [46]=市净率(PB)  [47]=涨停价
        # 注意: [45]/[46] 相邻但语义分别是「总市值/市净率」, 勿把 [46] 误当流通市值。
        volume = int(float(parts[6]))  # 已是「手」, 无需换算 (新浪 parts[8] 才是股)
        amount = float(parts[37]) * 10000 if parts[37] else 0  # 万元 → 元
        high = float(parts[33])
        low = float(parts[34])
        open_price = float(parts[5])
        turnover = float(parts[38]) if parts[38] else 0
        pe = float(parts[39]) if len(parts) > 39 and parts[39] else 0.0
        pb = float(parts[46]) if len(parts) > 46 and parts[46] else 0.0
        market_cap = float(parts[45]) * 1e8 if len(parts) > 45 and parts[45] else 0.0
        # 防列位移位再次静默出错: PB 正常区间 0~200, 越界大概率是取到了市值列。
        if pb > 1000:
            pb = 0.0
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
