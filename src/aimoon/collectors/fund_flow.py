"""Capital flow (资金面) collector with multi-source fallback.

Primary: akshare stock_individual_fund_flow (East Money, push2, may be blocked)
Fallback 1: akshare stock_fund_flow_individual (同花顺 ranking, per-page search)
Fallback 2: pysnowball

Each sub-fetcher independently; failures don't abort the pipeline.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from io import StringIO

import pandas as pd
import requests
from bs4 import BeautifulSoup

from ..models.stock import CapitalFlowData
from ..utils import resolve_market

try:
    from akshare.datasets import get_ths_js
except ImportError:
    get_ths_js = None


class FundFlowCollector:
    """Collects market capital flow data for a single A-share."""

    def __init__(self) -> None:
        self._sources_ok: list[str] = []

    async def fetch(self, symbol: str) -> CapitalFlowData:
        """Run all sub-fetchers; return aggregated CapitalFlowData."""
        data = CapitalFlowData(symbol=symbol)

        # Primary: pysnowball (3/5/10/20 day data)
        await self._fetch_via_pysnowball(symbol, data)
        await self._fetch_northbound(symbol, data)
        await self._fetch_lhb(symbol, data)

        # Fallback: akshare (daily order breakdown)
        if not data.main_net_5d and not data.net_3d:
            await self._fetch_individual_flow(symbol, data)

        data.source = "+".join(self._sources_ok) if self._sources_ok else "all_failed"
        return data

    def _parse_net(self, s: str) -> float:
        """Parse 同花顺 net flow string like '5.12亿', '-1234万' to float(yuan)."""
        s = s.strip().replace(",", "").replace(" ", "")
        if not s:
            return 0.0
        if "亿" in s:
            return float(s.replace("亿", "")) * 1e8
        if "万" in s:
            return float(s.replace("万", "")) * 1e4
        try:
            return float(s)
        except ValueError:
            return 0.0

    # ---------- 同花顺 fallback (early-termination pagination) ----------

    def _ths_fetch_page(self, url_template: str, page: int) -> pd.DataFrame:
        """Fetch a single 同花顺 ranking page; return parsed DataFrame or empty."""
        if get_ths_js is None:
            return pd.DataFrame()

        import py_mini_racer  # noqa: PLC0415

        js_code = py_mini_racer.MiniRacer()
        js_code.eval(get_ths_js("ths.js"))
        v_code = js_code.call("v")
        headers = {
            "Accept": "text/html, */*; q=0.01",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "hexin-v": v_code,
            "Host": "data.10jqka.com.cn",
            "Pragma": "no-cache",
            "Referer": "http://data.10jqka.com.cn/funds/hyzjl/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.85 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest",
        }
        r = requests.get(url_template.format(page), headers=headers, timeout=10)
        return pd.read_html(StringIO(r.text))[0]

    def _ths_search_stock(
        self, symbol: str, indicator: str, max_pages: int = 50
    ) -> dict | None:
        """Scan 同花顺 ranking pages for *symbol*; return first-match row dict or None.

        Early-termination: stops scanning once stock is found (much faster than
        fetching all ~104 pages for well-known stocks like 茅台).
        """
        indicator_urls = {
            "即时": (
                "http://data.10jqka.com.cn/funds/ggzjl/field/zdf/order/desc"
                "/page/{}/ajax/1/free/1/"
            ),
            "5日排行": (
                "http://data.10jqka.com.cn/funds/ggzjl/board/5/field/zdf/order/desc"
                "/page/{}/ajax/1/free/1/"
            ),
        }
        url_template = indicator_urls.get(indicator)
        if not url_template:
            return None

        page_num = self._ths_total_pages(url_template)
        if page_num is None:
            return None

        for page in range(1, min(page_num, max_pages) + 1):
            try:
                df = self._ths_fetch_page(url_template, page)
                if df is None or df.empty:
                    continue
                row = df[df.iloc[:, 1].astype(str) == symbol]
                if not row.empty:
                    return row.iloc[0].to_dict()
            except Exception:
                continue
        return None

    def _ths_total_pages(self, url_template: str) -> int | None:
        """Determine total page count from 同花顺 pagination info."""
        try:
            import py_mini_racer  # noqa: PLC0415
        except ImportError:
            return None

        js_code = py_mini_racer.MiniRacer()
        js_code.eval(get_ths_js("ths.js"))
        v_code = js_code.call("v")

        url = (
            "http://data.10jqka.com.cn/funds/ggzjl/field/code/order/desc/ajax/1/free/1/"
        )
        headers = {
            "hexin-v": v_code,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" " AppleWebKit/537.36"
            ),
        }
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, features="lxml")
        el = soup.find(name="span", attrs={"class": "page_info"})
        if el is None:
            return None
        parts = el.text.strip().split("/")
        return int(parts[1]) if len(parts) >= 2 else None

    async def _fetch_ths_ranking(self, symbol: str, data: CapitalFlowData) -> None:
        """Fallback: search 同花顺 ranking for target stock."""
        try:
            row_5d = await asyncio.to_thread(self._ths_search_stock, symbol, "5日排行")
            if row_5d:
                net_col = "资金流入净额" if "资金流入净额" in row_5d else "净额"
                data.main_net_5d = self._parse_net(str(row_5d.get(net_col, "0")))
                self._sources_ok.append("ths(ranking)")
        except Exception:
            pass

    # ---------- akshare sources ----------

    async def _fetch_individual_flow(self, symbol: str, data: CapitalFlowData) -> bool:
        """Fallback: fetch net flow from akshare. Returns True if data was fetched."""
        for attempt in range(2):
            try:
                df = await asyncio.to_thread(self._ak_individual_flow, symbol)
                if df is not None and not df.empty:
                    break
            except Exception:
                if attempt == 0:
                    await asyncio.sleep(1)
                    continue
                return False
        else:
            return await self._fetch_eastmoney_flow_http(symbol, data)

        try:
            if "主力净流入-净额" in df.columns:
                vals = df["主力净流入-净额"].values
                data.main_net_5d = float(sum(vals[-6:-1])) if len(vals) >= 6 else float(sum(vals[:-1]))  # noqa: E501
                if len(vals) >= 3:
                    data.net_3d = float(sum(vals[-3:]))
                if len(vals) >= 10:
                    data.net_10d = float(sum(vals[-10:]))
                if len(vals) >= 20:
                    data.net_20d = float(sum(vals[-20:]))

            self._sources_ok.append("akshare(个股资金流)")
            return True
        except Exception:
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
            "lmt": "5",
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
                data.main_net_5d = sum(all_main[-6:-1]) if len(all_main) >= 6 else sum(all_main[:-1])  # noqa: E501
            if len(all_main) >= 3:
                data.net_3d = sum(all_main[-3:])
            if len(all_main) >= 10:
                data.net_10d = sum(all_main[-10:])
            if len(all_main) >= 20:
                data.net_20d = sum(all_main[-20:])

            self._sources_ok.append("eastmoney(资金流HTTP)")
            return True
        except Exception:
            return False

    async def _fetch_northbound(self, symbol: str, data: CapitalFlowData) -> None:
        """北向资金持股变化 + 北向整体净流入."""
        # 1. 个股北向持股变化（东方财富 API，季度数据）
        try:
            cf_result = await asyncio.to_thread(self._em_northbound, symbol)
            if cf_result:
                data.northbound_chg = cf_result.get("change_value", 0.0)
                data.northbound_hold_shares = cf_result.get("hold_shares", 0.0)
                data.northbound_hold_value = cf_result.get("hold_value", 0.0)
                data.northbound_hold_ratio = cf_result.get("hold_ratio", 0.0)
                data.northbound_date = cf_result.get("date", "")
                self._sources_ok.append("eastmoney(北向持股)")
        except Exception:
            pass

        # 2. 北向整体净流入（沪深股通）
        try:
            df_flow = await asyncio.to_thread(self._ak_northbound_flow)
            if df_flow is not None and not df_flow.empty:
                north = df_flow[df_flow["资金方向"] == "北向"]
                if not north.empty:
                    total_net = north["成交净买额"].sum()
                    data.northbound_net_flow = float(total_net) * 1e8
                    if "eastmoney(北向持股)" not in self._sources_ok:
                        self._sources_ok.append("akshare(北向)")
        except Exception:
            pass

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
        try:
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
        except Exception:
            pass

    def _ak_lhb(self, symbol: str):
        import akshare as ak

        start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")
        return ak.stock_lhb_detail_em(symbol=symbol, start_date=start, end_date=end)

    # ---------- pysnowball fallback ----------

    async def _fetch_via_pysnowball(self, symbol: str, data: CapitalFlowData) -> None:
        """Fetch 3/5/10/20 day net flow via pysnowball."""
        try:
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
        except Exception:
            pass


def capital_flow_score(cf: CapitalFlowData) -> tuple[int, str, str]:
    """Rule-based 1-5 capital-flow score.

    Returns (score 1-5, detail_text, main_force_label).
    Label is one of "流入"/"流出"/"持平".
    """
    main_5d = cf.main_net_5d

    if main_5d > 0:
        main_force = "流入"
    elif main_5d < 0:
        main_force = "流出"
    else:
        main_force = "持平"

    if main_5d > 5e8:
        s1 = 5
    elif main_5d > 1e8:
        s1 = 4
    elif main_5d > -1e8:
        s1 = 3
    elif main_5d > -5e8:
        s1 = 2
    else:
        s1 = 1

    # Trend: 3d vs 10d direction consistency
    trend_score = 0
    if cf.net_3d > 0 and cf.net_10d > 0:
        trend_score = 2
    elif cf.net_3d < 0 and cf.net_10d < 0:
        trend_score = -1

    # 20d long-term trend
    long_score = 0
    if cf.net_20d > 5e8:
        long_score = 2
    elif cf.net_20d > 0:
        long_score = 1
    elif cf.net_20d < -5e8:
        long_score = -2
    elif cf.net_20d < 0:
        long_score = -1

    if cf.northbound_chg != 0:
        s4 = (
            5
            if cf.northbound_chg > 1e8
            else (
                4 if cf.northbound_chg > 0 else (2 if cf.northbound_chg > -1e8 else 1)
            )
        )  # noqa: E501
    else:
        s4 = 3

    if cf.lhb_date and cf.lhb_net_buy > 0:
        s5 = 5
    elif cf.lhb_date and cf.lhb_net_buy < 0:
        s5 = 2
    else:
        s5 = 3

    total = s1 * 0.35 + trend_score + long_score + s4 * 0.15 + s5 * 0.05
    total = max(1, min(5, 3 + total))
    score = max(1, min(5, round(total)))

    parts = [
        f"近5日主力净流入{main_5d / 1e8:.2f}亿",
        f"3日{cf.net_3d / 1e8:+.2f}亿",
        f"10日{cf.net_10d / 1e8:+.2f}亿",
        f"20日{cf.net_20d / 1e8:+.2f}亿",
    ]
    if cf.northbound_chg:
        nb = cf.northbound_chg / 1e8
        parts.append(f"北向变化{nb:+.2f}亿")
    if cf.lhb_date:
        parts.append(f"龙虎榜({cf.lhb_date})净买{cf.lhb_net_buy / 1e8:.2f}亿")

    detail = "；".join(parts) + "。"
    return score, detail, main_force
