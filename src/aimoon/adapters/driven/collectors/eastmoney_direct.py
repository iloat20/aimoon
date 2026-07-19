"""东方财富官方免鉴权HTTP接口采集器 — 作为 akshare 的 fallback 数据源。

东方财富开放了一批免鉴权的公开 HTTP 接口,可直接调用,无需 akshare 中间层:

- 历史K线: https://push2his.eastmoney.com/api/qt/stock/kline/get
- 实时行情: https://push2.eastmoney.com/api/qt/stock/get

字段采用数字编码(f51=日期, f52=开盘, f53=收盘, ...),本模块负责解码。
接口免注册,但仍需控制请求频率(建议 ≥0.5s 间隔)以避免限流。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from aimoon.adapters.driven.collectors.base import DataCollector
from aimoon.adapters.driven.common.retry import silent_failure
from aimoon.core.domain.entities.kline import KlineData
from aimoon.core.domain.value_objects.kline_bar import KlineBar

logger = logging.getLogger(__name__)

# 东方财富 K线接口 fields2 字段解码表。
# f51=日期, f52=开盘, f53=收盘, f54=最高, f55=最低,
# f56=成交量, f57=成交额, f58=振幅(%), f59=涨跌幅(%), f60=涨跌额, f61=换手率(%)
_KLINE_FIELD_MAP = {
    "f51": "date",
    "f52": "open",
    "f53": "close",
    "f54": "high",
    "f55": "low",
    "f56": "volume",
    "f57": "amount",
    "f58": "amplitude",
    "f59": "pct_change",
    "f60": "change",
    "f61": "turnover",
}

_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


class EastMoneyDirectCollector(DataCollector[KlineData]):
    """东方财富免鉴权 K 线采集器 — fallback 数据源。

    调用东方财富官方 push2 接口,解码 f1-f170 数字编码字段。
    当 akshare 全部失败(被 WAF 拦截/超时)时,本采集器作为最后手段。
    """

    name = "kline_eastmoney_direct"

    def __init__(self, days: int = 180, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(client)
        self._days = days
        self._request_interval = 0.5
        self._init_proxy_patch()

    def _init_proxy_patch(self) -> None:
        """若用户在 .env 配置了代理,尝试通过代理绕过 WAF。

        东方财富同样会 WAF 拦截异常请求,通过代理服务商中转可解决。
        """
        try:
            from aimoon.adapters.driven.config.settings import get_settings

            settings = get_settings()
            auth_ip = getattr(settings, "akshare_proxy_auth_ip", "")
            auth_token = getattr(settings, "akshare_proxy_auth_token", "")
            if auth_ip:
                import akshare_proxy_patch

                akshare_proxy_patch.install_patch(
                    auth_ip=auth_ip,
                    auth_token=auth_token,
                    retry=30,
                    timeout=5,
                    fast=True,
                )
                logger.info("[eastmoney_direct] 代理补丁已启用: auth_ip=%s", auth_ip)
        except ImportError:
            pass
        except Exception as e:
            logger.warning("[eastmoney_direct] 代理补丁初始化失败: %s", e)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=15.0,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Referer": "https://quote.eastmoney.com/",
                    "Accept": "application/json, text/plain, */*",
                },
            )
        return self._client

    def _market_prefix(self, symbol: str) -> str:
        """东方财富市场前缀: 1=上海, 0=深圳, 2=北京。"""
        if symbol.startswith("6"):
            return "1"
        if symbol.startswith(("0", "3")):
            return "0"
        return "2"

    async def _throttle(self) -> None:
        """请求间隔控制。"""
        await asyncio.sleep(self._request_interval)

    async def fetch(self, symbol: str, **kwargs: Any) -> KlineData:
        """Fetch K-line via 东方财富 push2his API."""
        await self._throttle()
        with silent_failure("kline_eastmoney_direct"):
            bars = await self._fetch_klines(symbol)
            if bars:
                return KlineData(
                    symbol=symbol,
                    bars=bars[-self._days:],
                    source="eastmoney(direct)",
                    period="daily",
                )
        return KlineData(symbol=symbol, source="all_failed")

    async def _fetch_klines(self, symbol: str) -> list[KlineBar]:
        """调用 push2his 接口,解码 f51-f61 字段。"""
        secid = f"{self._market_prefix(symbol)}.{symbol}"
        # 请求天数略多于目标,留 buffer
        lmt = self._days + 10
        # fields1: f1=代码,f2=名称,f3=最新价,f4=最高,f5=最低
        # fields2: f51=日期,f52=开盘,...,f61=换手率
        fields2 = ",".join(sorted(_KLINE_FIELD_MAP.keys(), key=lambda x: int(x[1:])))
        params: dict[str, str | int] = {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5",
            "fields2": fields2,
            "klt": "101",  # 日K
            "fqt": "1",    # 前复权
            "end": "20500101",
            "lmt": lmt,
        }
        client = await self._get_client()
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                resp = await client.get(_KLINE_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
                klines_raw = data.get("data", {})
                if not klines_raw or not klines_raw.get("klines"):
                    return []
                return self._decode_klines(klines_raw["klines"])
            except Exception as e:
                last_err = e
                wait = 2 ** attempt
                logger.debug(
                    "[eastmoney_direct] %s 第 %d 次失败: %s,等待 %ds",
                    symbol, attempt + 1, e, wait,
                )
                await asyncio.sleep(wait)
        logger.warning("[eastmoney_direct] %s 3 次全部失败: %s", symbol, last_err)
        return []

    @staticmethod
    def _decode_klines(klines: list[str]) -> list[KlineBar]:
        """解码东方财富 K 线字符串序列。

        每根 K 线是逗号分隔字符串,顺序对应 fields2 字段列表:
        f51日期,f52开盘,f53收盘,f54最高,f55最低,f56成交量,f57成交额,f58振幅,f59涨跌幅,f60涨跌额,f61换手率
        """
        bars: list[KlineBar] = []
        for line in klines:
            parts = line.split(",")
            if len(parts) < 7:
                continue
            # 按 fields2 排序后的顺序解码
            try:
                bars.append(
                    KlineBar(
                        date=parts[0][:10],
                        open=float(parts[1]),
                        close=float(parts[2]),
                        high=float(parts[3]),
                        low=float(parts[4]),
                        volume=float(parts[5]),
                        amount=float(parts[6]),
                        pct_change=float(parts[8]) if len(parts) > 8 else 0.0,
                    )
                )
            except (ValueError, IndexError):
                continue
        return bars
