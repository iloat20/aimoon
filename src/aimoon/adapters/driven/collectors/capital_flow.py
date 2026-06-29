"""Capital flow (资金面) collector with multi-source fallback.

Primary: pysnowball capital_history (3/5/10/20 day data)
Fallback: akshare stock_individual_fund_flow (East Money)

Each sub-fetcher runs independently; failures don't abort the pipeline.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

import httpx

from aimoon.adapters.driven.common.retry import silent_failure
from aimoon.core.domain.entities.capital_flow import CapitalFlowData

from .base import DataCollector


class CapitalFlowCollector(DataCollector[CapitalFlowData]):
    """Collects market capital flow data for a single A-share."""

    name = "fund_flow"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._sources_ok: list[str] = []
        self._client_provided = client is not None
        self._client = client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client

    async def fetch(self, symbol: str, **kwargs: Any) -> CapitalFlowData:
        """Run sub-fetchers with smart fallback; return aggregated CapitalFlowData."""
        self._sources_ok = []  # H2: reset per call
        data = CapitalFlowData(symbol=symbol)
        sources: list[str] = []  # M5: local list, no shared state mutation

        # 1. 先运行 pysnowball（主源）
        await self._fetch_via_pysnowball(symbol, data, sources)

        # 2. 并行运行：akshare（fallback）+ northbound + lhb
        #    akshare 内部有判断：如果 pysnowball 已贡献数据则直接返回
        results = await asyncio.gather(
            self._fetch_via_akshare(symbol, data, sources),
            self._fetch_northbound(symbol, data, sources),
            self._fetch_lhb(symbol, data, sources),
            return_exceptions=True,
        )

        for r in results:
            if isinstance(r, Exception):
                logging.warning("[capital_flow_subfetch] %s: %s", type(r).__name__, r)

        self._sources_ok = sources
        data.source = "+".join(self._sources_ok) if self._sources_ok else "all_failed"
        return data

    # ---------- pysnowball primary source ----------

    async def _fetch_via_pysnowball(
        self, symbol: str, data: CapitalFlowData, sources: list[str]
    ) -> None:
        """Fetch 3/5/10/20 day net flow via pysnowball capital_history."""
        try:
            result = await asyncio.to_thread(self._call_pysnowball, symbol)
            if not result:
                return
            data_sum = result.get("data") or {}
            if data_sum.get("sum5"):
                data.main_net_5d = float(data_sum["sum5"])
            if data_sum.get("sum3"):
                data.main_net_3d = float(data_sum["sum3"])
            if data_sum.get("sum10"):
                data.main_net_10d = float(data_sum["sum10"])
            if data_sum.get("sum20"):
                data.main_net_20d = float(data_sum["sum20"])
            if any([data.main_net_5d, data.main_net_3d, data.main_net_10d, data.main_net_20d]):
                sources.append("pysnowball(雪球)")
        except Exception as e:
            logging.warning("[pysnowball_capital_flow] %s: %s", type(e).__name__, e)

    def _call_pysnowball(self, symbol: str) -> dict:
        """Call pysnowball capital_history API."""
        import pysnowball as ball

        from aimoon.adapters.driven.config.settings import get_settings

        # Ensure token is set
        settings = get_settings()
        if settings.xueqiu_token:
            ball.set_token(settings.xueqiu_token)

        # pysnowball requires symbol with market prefix (e.g., SH600519)
        if symbol.startswith("6"):
            ball_symbol = f"SH{symbol}"
        elif symbol.startswith(("0", "3")):
            ball_symbol = f"SZ{symbol}"
        else:
            ball_symbol = f"BJ{symbol}"

        return ball.capital_history(ball_symbol, count=20)

    # ---------- akshare fallback ----------

    async def _fetch_via_akshare(
        self, symbol: str, data: CapitalFlowData, sources: list[str]
    ) -> None:
        """Fallback: fetch net flow from akshare individual fund flow."""
        try:
            from ..financial.akshare_adapter import AkshareFinancialAdapter

            adapter = AkshareFinancialAdapter()
            cf = await adapter.fetch_capital_flow(symbol)
            if cf:
                data.main_net_5d = cf.get("main_net_5d", 0.0)
                data.main_net_3d = cf.get("main_net_3d", 0.0)
                data.main_net_10d = cf.get("main_net_10d", 0.0)
                data.main_net_20d = cf.get("main_net_20d", 0.0)
                sources.append("akshare(个股资金流)")
        except Exception as e:
            logging.warning("[akshare_capital_flow_fallback] %s: %s", type(e).__name__, e)

    async def _fetch_northbound(
        self, symbol: str, data: CapitalFlowData, sources: list[str]
    ) -> None:
        """北向资金持股变化 + 北向整体净流入."""
        # 1. 个股北向持股变化（东方财富 API，季度数据）
        with silent_failure("eastmoney_northbound_holdings"):
            cf_result = await self._em_northbound(symbol)
            if cf_result:
                data.northbound_chg = cf_result.get("change_value", 0.0)
                data.northbound_hold_shares = cf_result.get("hold_shares", 0.0)
                data.northbound_hold_value = cf_result.get("hold_value", 0.0)
                data.northbound_hold_ratio = cf_result.get("hold_ratio", 0.0)
                data.northbound_date = cf_result.get("date", "")
                sources.append("eastmoney(北向持股)")

        # 2. 北向整体净流入（沪深股通）
        with silent_failure("akshare_northbound_flow"):
            df_flow = await asyncio.to_thread(self._ak_northbound_flow)
            if df_flow is not None and not df_flow.empty:
                north = df_flow[df_flow["资金方向"] == "北向"]
                if not north.empty:
                    total_net = north["成交净买额"].sum()
                    data.northbound_net_flow = float(total_net) * 1e8  # 单位: 亿元→元
                    if "eastmoney(北向持股)" not in sources:
                        sources.append("akshare(北向)")

    async def _em_northbound(self, symbol: str) -> dict:
        """东方财富 API 获取个股北向持股（季度数据）."""
        from aimoon.adapters.driven.config.settings import get_settings

        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "reportName": "RPT_MUTUAL_HOLDSTOCKNORTH_STA",
            "columns": "ALL",
            "filter": f'(SECURITY_CODE="{symbol}")',
            "pageSize": "2",
            "sortColumns": "TRADE_DATE",
            "sortTypes": "-1",
            "source": "WEB",
            "client": "WEB",
        }
        headers = {
            "User-Agent": get_settings().default_user_agent,
            "Referer": "https://data.eastmoney.com/",
        }
        client = await self._get_client()
        resp = await client.get(url, params=params, headers=headers)
        data = resp.json()
        result = data.get("result") or {}
        items = result.get("data") or []
        if not items:
            return {}

        item = items[0]
        hold_shares = float(item.get("HOLD_SHARES") or 0)
        hold_value = float(item.get("HOLD_MARKET_CAP") or 0)
        hold_ratio = float(item.get("A_SHARES_RATIO") or 0)
        date = str(item.get("TRADE_DATE", ""))[:10]

        # H7: compute change from previous period
        change_value = 0.0
        if len(items) >= 2:
            prev_value = float(items[1].get("HOLD_MARKET_CAP") or 0)
            change_value = hold_value - prev_value

        return {
            "hold_shares": hold_shares,
            "hold_value": hold_value,
            "hold_ratio": hold_ratio,
            "change_value": change_value,
            "date": date,
        }

    def _ak_northbound_flow(self):
        import akshare as ak

        return ak.stock_hsgt_fund_flow_summary_em()

    async def _fetch_lhb(self, symbol: str, data: CapitalFlowData, sources: list[str]) -> None:
        """龙虎榜（最近上榜记录）. Uses stock_lhb_detail_em for the last ~30 days."""
        with silent_failure("akshare_lhb"):
            df = await asyncio.to_thread(self._ak_lhb, symbol)
            if df is None or df.empty:
                return

            latest = df.iloc[0]

            for k in ("上榜日", "日期"):
                if k in df.columns:
                    d = latest.get(k)
                    data.lhb_date = str(d)[:10] if d else ""
                    break

            for k in ("解读", "上榜原因"):
                if k in df.columns:
                    data.lhb_reason = str(latest.get(k, "") or "")
                    break

            for k in ("龙虎榜净买额", "净买额"):
                if k in df.columns:
                    data.lhb_net_buy = float(latest.get(k, 0) or 0)
                    break

            sources.append("akshare(龙虎榜)")

    def _ak_lhb(self, symbol: str):
        import akshare as ak

        start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")
        df = ak.stock_lhb_detail_em(start_date=start, end_date=end)
        if df is None or df.empty:
            return df
        if "代码" not in df.columns:
            import pandas as pd

            return pd.DataFrame()
        return df[df["代码"] == symbol]
