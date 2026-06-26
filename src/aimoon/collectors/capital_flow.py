"""Capital flow (资金面) collector with multi-source fallback.

Primary: pysnowball (3/5/10/20 day data)
Fallback: akshare stock_individual_fund_flow (East Money)
HTTP fallback: East Money push2his API

Each sub-fetcher runs independently; failures don't abort the pipeline.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from ..models.stock import CapitalFlowData
from ..utils import resolve_market, silent_failure
from .base import DataCollector


class CapitalFlowCollector(DataCollector[CapitalFlowData]):
    """Collects market capital flow data for a single A-share."""

    name = "fund_flow"

    def __init__(self) -> None:
        self._sources_ok: list[str] = []

    async def fetch(self, symbol: str, **kwargs: Any) -> CapitalFlowData:
        """Run all sub-fetchers; return aggregated CapitalFlowData."""
        data = CapitalFlowData(symbol=symbol)

        # Primary sources: run concurrently
        await asyncio.gather(
            self._fetch_via_pysnowball(symbol, data),
            self._fetch_northbound(symbol, data),
            self._fetch_lhb(symbol, data),
        )

        # Fallback: akshare (daily order breakdown)
        if not data.main_net_5d and not data.net_3d:
            await self._fetch_individual_flow(symbol, data)

        data.source = "+".join(self._sources_ok) if self._sources_ok else "all_failed"
        return data

    # ---------- akshare sources ----------

    async def _fetch_individual_flow(self, symbol: str, data: CapitalFlowData) -> bool:
        """Fallback: fetch net flow from akshare. Returns True if data was fetched."""
        for attempt in range(2):
            try:
                df = await asyncio.to_thread(self._ak_individual_flow, symbol)
                if df is not None and not df.empty:
                    break
            except Exception as e:
                logging.warning(
                    "[akshare_individual_flow_attempt_%d] %s: %s",
                    attempt + 1,
                    type(e).__name__,
                    e,
                )
                if attempt == 0:
                    await asyncio.sleep(1)
        else:
            return await self._fetch_eastmoney_flow_http(symbol, data)

        try:
            if "主力净流入-净额" in df.columns:
                vals = df["主力净流入-净额"].values
                data.main_net_5d = float(sum(vals[-5:])) if len(vals) >= 5 else float(sum(vals))  # noqa: E501
                if len(vals) >= 3:
                    data.net_3d = float(sum(vals[-3:]))
                if len(vals) >= 10:
                    data.net_10d = float(sum(vals[-10:]))
                if len(vals) >= 20:
                    data.net_20d = float(sum(vals[-20:]))

            self._sources_ok.append("akshare(个股资金流)")
            return True
        except Exception as e:
            logging.warning(
                "[akshare_individual_flow_parse] %s: %s",
                type(e).__name__,
                e,
            )
            return False

    def _ak_individual_flow(self, symbol: str):
        import akshare as ak

        return ak.stock_individual_fund_flow(
            stock=symbol, market=resolve_market(symbol).lower()
        )

    async def _fetch_eastmoney_flow_http(
        self, symbol: str, data: CapitalFlowData
    ) -> bool:
        """Direct East Money HTTP API for capital flow."""
        import httpx

        market = "1" if symbol.startswith("6") else "0"
        secid = f"{market}.{symbol}"

        url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
        params = {
            "lmt": "20",
            "klt": "101",
            "secid": secid,
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        }
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://data.eastmoney.com/",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code != 200:
                    return False
                result = resp.json()

            klines = result.get("data", {}).get("klines", [])
            if not klines:
                return False

            # Format: "日期,主力净流入,小单净流入,中单净流入,大单净流入,超大单净流入"
            # Calculate multi-period sums from klines
            all_main = [float(k.split(",")[1] or 0) for k in klines if len(k.split(",")) >= 2]  # noqa: E501
            if len(all_main) >= 2:
                data.main_net_5d = sum(all_main[-5:]) if len(all_main) >= 5 else sum(all_main)  # noqa: E501
            if len(all_main) >= 3:
                data.net_3d = sum(all_main[-3:])
            if len(all_main) >= 10:
                data.net_10d = sum(all_main[-10:])
            if len(all_main) >= 20:
                data.net_20d = sum(all_main[-20:])

            self._sources_ok.append("eastmoney(资金流HTTP)")
            return True
        except Exception as e:
            logging.warning("[eastmoney_flow_http] %s: %s", type(e).__name__, e)
            return False

    async def _fetch_northbound(self, symbol: str, data: CapitalFlowData) -> None:
        """北向资金持股变化 + 北向整体净流入."""
        # 1. 个股北向持股变化（东方财富 API，季度数据）
        with silent_failure("eastmoney_northbound_holdings"):
            cf_result = await asyncio.to_thread(self._em_northbound, symbol)
            if cf_result:
                data.northbound_chg = cf_result.get("change_value", 0.0)
                data.northbound_hold_shares = cf_result.get("hold_shares", 0.0)
                data.northbound_hold_value = cf_result.get("hold_value", 0.0)
                data.northbound_hold_ratio = cf_result.get("hold_ratio", 0.0)
                data.northbound_date = cf_result.get("date", "")
                self._sources_ok.append("eastmoney(北向持股)")

        # 2. 北向整体净流入（沪深股通）
        with silent_failure("akshare_northbound_flow"):
            df_flow = await asyncio.to_thread(self._ak_northbound_flow)
            if df_flow is not None and not df_flow.empty:
                north = df_flow[df_flow["资金方向"] == "北向"]
                if not north.empty:
                    total_net = north["成交净买额"].sum()
                    data.northbound_net_flow = float(total_net) * 1e8
                    if "eastmoney(北向持股)" not in self._sources_ok:
                        self._sources_ok.append("akshare(北向)")

    def _em_northbound(self, symbol: str) -> dict:
        """东方财富 API 获取个股北向持股（季度数据）."""
        import httpx

        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "reportName": "RPT_MUTUAL_HOLDSTOCKNORTH_STA",
            "columns": "ALL",
            "filter": f'(SECURITY_CODE="{symbol}")',
            "pageSize": "1",
            "sortColumns": "TRADE_DATE",
            "sortTypes": "-1",
            "source": "WEB",
            "client": "WEB",
        }
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://data.eastmoney.com/",
        }

        resp = httpx.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()
        result = data.get("result") or {}
        items = result.get("data") or []
        if not items:
            return {}

        item = items[0]
        hold_shares = float(item.get("HOLD_SHARES") or 0)
        hold_value = float(item.get("HOLD_MARKET_CAP") or 0)
        hold_ratio = float(item.get("A_SHARES_RATIO") or 0)
        change_rate = float(item.get("CHANGE_RATE") or 0)
        date = str(item.get("TRADE_DATE", ""))[:10]

        # Calculate change value from change rate and market cap
        change_value = hold_value * change_rate / 100 if hold_value else 0

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

    async def _fetch_lhb(self, symbol: str, data: CapitalFlowData) -> None:
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

            self._sources_ok.append("akshare(龙虎榜)")

    def _ak_lhb(self, symbol: str):
        import akshare as ak

        start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")
        df = ak.stock_lhb_detail_em(start_date=start, end_date=end)
        if df is None or df.empty:
            return df
        # API no longer accepts symbol param; filter by code column
        return df[df["代码"] == symbol] if "代码" in df.columns else df

    # ---------- pysnowball fallback ----------

    async def _fetch_via_pysnowball(self, symbol: str, data: CapitalFlowData) -> None:
        """Fetch 3/5/10/20 day net flow via pysnowball."""
        with silent_failure("pysnowball_capital_flow"):
            from ..financial.pysnowball_adapter import PysnowballAdapter

            adapter = PysnowballAdapter()
            cf = await adapter.fetch_capital_flow(symbol)
            if not cf:
                return
            data.main_net_5d = cf.get("main_net_5d", 0.0)
            data.net_3d = cf.get("net_3d", 0.0)
            data.net_10d = cf.get("net_10d", 0.0)
            data.net_20d = cf.get("net_20d", 0.0)
            self._sources_ok.append("pysnowball(资金流)")
