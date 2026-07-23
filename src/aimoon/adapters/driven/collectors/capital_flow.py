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

from aimoon.adapters.driven.common.cache import DiskTtlCache
from aimoon.adapters.driven.common.retry import silent_failure
from aimoon.core.application.ports import CapitalFlowSource
from aimoon.core.domain.entities.capital_flow import CapitalFlowData

from .base import DataCollector

logger = logging.getLogger(__name__)

# 龙虎榜全市场拉取代价极高(一次拉取近 30 天全市场数据再本地过滤)。
# 同一天内数据不变,故按 (start,end) 缓存 1 天,后续任意标的/重复运行直接命中,
# 不再重拉全市场。结果与原逻辑逐字节一致(同样的全市场 df 本地按代码过滤)。
_LHB_CACHE: DiskTtlCache | None = None


def _lhb_cache() -> DiskTtlCache:
    global _LHB_CACHE
    if _LHB_CACHE is None:
        _LHB_CACHE = DiskTtlCache(namespace="lhb", ttl_seconds=86400)
    return _LHB_CACHE


class CapitalFlowCollector(DataCollector[CapitalFlowData]):
    """Collects market capital flow data for a single A-share."""

    name = "fund_flow"

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        financial_adapter: CapitalFlowSource | None = None,
    ) -> None:
        super().__init__(client)
        self._sources_ok: list[str] = []
        # 经构造函数注入的资金流数据源端口(由 orchestrator 注入共享的财务适配器,
        # 复用其磁盘缓存,避免每次 new 一个具体适配器)。collectors 不再依赖具体实现。
        self._financial_adapter = financial_adapter

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
            # 雪球 capital_history 返回 {data: {columns:[...], item:[[...]]}}
            # 列名形如 sum3/sum5/sum10/sum20（N 日主力累计净流入,单位:元,与 akshare 兜底一致）
            rows = data_sum.get("item")
            if isinstance(rows, list) and rows:
                cols = [str(c).lower() for c in (data_sum.get("columns") or [])]
                last = rows[-1]
                mapping = (
                    ("main_net_5d", "sum5"),
                    ("main_net_3d", "sum3"),
                    ("main_net_10d", "sum10"),
                    ("main_net_20d", "sum20"),
                )
                for attr, col in mapping:
                    if col in cols:
                        try:
                            v = last[cols.index(col)]
                        except (IndexError, TypeError):
                            v = None
                        if v is not None:
                            setattr(data, attr, float(v))
            elif data_sum:  # 兜底:扁平结构(旧假设)
                for attr, key in (
                    ("main_net_5d", "sum5"),
                    ("main_net_3d", "sum3"),
                    ("main_net_10d", "sum10"),
                    ("main_net_20d", "sum20"),
                ):
                    if data_sum.get(key):
                        setattr(data, attr, float(data_sum[key]))
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
        """Fallback: fetch net flow via the injected capital-flow source port.

        数据源由 orchestrator 经构造函数注入(共享其年报/季报/历史磁盘缓存)。
        若未注入(standalone 构造),优雅跳过该回退源,不构造具体适配器。
        """
        adapter = self._financial_adapter
        if adapter is None:
            return
        try:
            cf = await adapter.fetch_capital_flow(symbol)
            if cf:
                if data.main_net_5d == 0.0:
                    data.main_net_5d = cf.get("main_net_5d", 0.0)
                if data.main_net_3d == 0.0:
                    data.main_net_3d = cf.get("main_net_3d", 0.0)
                if data.main_net_10d == 0.0:
                    data.main_net_10d = cf.get("main_net_10d", 0.0)
                if data.main_net_20d == 0.0:
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

        # 注:原「北向整体净流入(沪深股通)」分支取的是全市场北向净买额,并非个股
        # 北向持股变动,语义错误;northbound_net_flow 字段已移除(无消费端),
        # 以免误导 sources 标注。个股级北向见上方 _em_northbound。

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
        import pandas as pd

        start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")
        cache_key = f"lhb:{start}:{end}"

        records = _lhb_cache().get(cache_key)
        if records is None:
            df = ak.stock_lhb_detail_em(start_date=start, end_date=end)
            if df is None or df.empty:
                return df
            records = df.to_dict("records")
            _lhb_cache().set(cache_key, records)
        else:
            df = pd.DataFrame.from_records(records)

        if df is None or df.empty:
            return df
        if "代码" not in df.columns:
            return pd.DataFrame()
        return df[df["代码"] == symbol]
