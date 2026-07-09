"""东方财富 datacenter-web 免费 API — 资产负债表详细数据(应收账款/存货/应付)。

该接口免鉴权、在当前网络环境下可达(不被 WAF 拦截),字段齐全:
- ACCOUNTS_RECE = 应收账款
- INVENTORY = 存货
- ACCOUNTS_PAYABLE = 应付账款
- TOTAL_ASSETS / FIXED_ASSET / MONETARYFUNDS
- CURRENT_RATIO / DEBT_ASSET_RATIO / TOTAL_LIABILITIES

解决了 akshare 采集器中"应收账款/存货/应付"数据缺失的核心问题。
限速:按源 IP 限流,请求间隔 ≥1 秒。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from aimoon.adapters.driven.common.cache import DiskTtlCache

logger = logging.getLogger(__name__)

_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_BALANCE_RPT = "RPT_DMSK_FN_BALANCE"
_FN_CPD_RPT = "RPT_LICO_FN_CPD"


class DatacenterWebCollector:
    """东方财富 datacenter-web 财务数据采集器(免鉴权,WAF 友好)。

    优先用于获取应收账款/存货/应付账款详细数据,这些数据在 akshare
    标准接口中经常缺失。akshare 失败时作为 fallback。
    """

    def __init__(self, request_interval: float = 1.0) -> None:
        self._request_interval = request_interval
        self._cache = DiskTtlCache(
            namespace="datacenter_web",
            ttl_seconds=86400,
        )

    def _market_prefix(self, symbol: str) -> str:
        if symbol.startswith("6"):
            return "1"
        if symbol.startswith(("0", "3")):
            return "0"
        return "2"

    async def _throttle(self) -> None:
        await asyncio.sleep(self._request_interval)

    async def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        """调用 datacenter-web,解析 JSON 响应。"""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://data.eastmoney.com/",
        }
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            r = await client.get(_DATACENTER_URL, params=params)
            r.raise_for_status()
            return r.json()

    async def fetch_balance_sheet(
        self, symbol: str, periods: int = 8,
    ) -> list[dict[str, Any]]:
        """获取近 N 期资产负债表详细数据。

        Returns: 按 REPORT_DATE 倒序排列的字段字典列表。
        """
        cache_key = f"balance:{symbol}:{periods}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        await self._throttle()
        params = {
            "reportName": _BALANCE_RPT,
            "columns": "ALL",
            "source": "WEB",
            "client": "WEB",
            "filter": f'(SECURITY_CODE="{symbol}")',
            "pageSize": periods,
            "sortColumns": "REPORT_DATE",
            "sortTypes": "-1",
        }
        try:
            data = await self._get(params)
            rows = (data.get("result") or {}).get("data") or []
            # 解码字段名为可读格式
            decoded = [self._decode_balance_row(r) for r in rows]
            if decoded:
                self._cache.set(cache_key, decoded)
            return decoded
        except Exception as e:
            logger.warning("[datacenter-web] balance fetch failed: %s", e)
            return []

    def _decode_balance_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """解码单行资产负债表数据。"""
        return {
            "report_date": str(row.get("REPORT_DATE", ""))[:10],
            "total_assets": row.get("TOTAL_ASSETS"),
            "accounts_receivable": row.get("ACCOUNTS_RECE"),
            "accounts_receivable_ratio": row.get("ACCOUNTS_RECE_RATIO"),
            "inventory": row.get("INVENTORY"),
            "inventory_ratio": row.get("INVENTORY_RATIO"),
            "accounts_payable": row.get("ACCOUNTS_PAYABLE"),
            "accounts_payable_ratio": row.get("ACCOUNTS_PAYABLE_RATIO"),
            "fixed_assets": row.get("FIXED_ASSET"),
            "monetary_funds": row.get("MONETARYFUNDS"),
            "total_liabilities": row.get("TOTAL_LIABILITIES"),
            "total_equity": row.get("TOTAL_EQUITY"),
            "current_ratio": row.get("CURRENT_RATIO"),
            "debt_asset_ratio": row.get("DEBT_ASSET_RATIO"),
        }

    async def fetch_financial_summary(
        self, symbol: str, periods: int = 4,
    ) -> list[dict[str, Any]]:
        """获取近 N 期财务摘要(EPS/营收/净利/ROE/BPS)。"""
        await self._throttle()
        params = {
            "reportName": _FN_CPD_RPT,
            "columns": "ALL",
            "source": "WEB",
            "client": "WEB",
            "filter": f'(SECURITY_CODE="{symbol}")',
            "pageSize": periods,
            "sortColumns": "REPORT_DATE",
            "sortTypes": "-1",
        }
        try:
            data = await self._get(params)
            rows = (data.get("result") or {}).get("data") or []
            return [
                {
                    "report_date": str(r.get("REPORTDATE", ""))[:10],
                    "quarter": r.get("QDATE"),
                    "eps": r.get("BASIC_EPS"),
                    "revenue": r.get("TOTAL_OPERATE_INCOME"),
                    "revenue_yoy": r.get("YSTZ"),
                    "net_profit": r.get("PARENT_NETPROFIT"),
                    "net_profit_yoy": r.get("SJLTZ"),
                    "roe": r.get("WEIGHTAVG_ROE"),
                    "bps": r.get("BPS"),
                    "gross_margin": r.get("XSMLL"),
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning("[datacenter-web] financial summary failed: %s", e)
            return []
