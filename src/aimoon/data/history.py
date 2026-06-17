"""历史 K 线 — mootdx（TCP 直连）→ 腾讯（HTTP）→ AKShare（个股/指数）"""

from __future__ import annotations

import logging
import threading
import time

import akshare as ak
import httpx
import pandas as pd
import pendulum
from tenacity import retry, stop_after_attempt, wait_exponential

from aimoon.cache import DataCache
from aimoon.data.validator import fix_kline_dates  # re-export for backward compat
from aimoon.result import Err, Ok, Result

logger = logging.getLogger(__name__)

# 限制 AKShare 并发请求数，避免服务端限流
_akshare_semaphore = threading.Semaphore(5)


def _tencent_kline(stock_code: str | int, days: int) -> Result[pd.DataFrame, str]:
    """腾讯 K 线数据获取"""
    stock_code = str(stock_code)  # 确保是字符串
    prefix = "sh" if stock_code.startswith("6") else "sz"
    secid = prefix + stock_code
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {"param": f"{secid},day,,,{days},qfq"}
    try:
        r = httpx.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            return Err(f"{stock_code}: Tencent API error")
        inner = data["data"].get(secid, {})
        key = "day" if "day" in inner else "qfqday"
        klines = inner.get(key, [])
        if not klines:
            return Err(f"{stock_code}: empty Tencent data")
        rows = [
            {
                "date": k[0],
                "open": float(k[1]),
                "close": float(k[2]),
                "high": float(k[3]),
                "low": float(k[4]),
                "volume": float(k[5]),
            }
            for k in klines
        ]
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        for col in ("amount", "amplitude", "pct_change", "change", "turnover"):
            df[col] = 0.0
        logger.info(
            "Tencent data for %s: amount/pct_change/amplitude/change/turnover are zero-filled "
            "(Tencent API does not provide these fields); volume is accurate",
            stock_code,
        )
        return Ok(df)
    except Exception as e:
        return Err(f"{stock_code}: Tencent fallback failed: {e}")


def _akshare_kline(
    code: str, start_date: str, end_date: str, retries: int = 2
) -> Result[pd.DataFrame, str]:
    """AKShare 获取 K 线，带信号量限流 + tenacity 重试。"""

    @retry(
        stop=stop_after_attempt(retries + 1),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
        reraise=True,
    )
    def _fetch() -> pd.DataFrame:
        with _akshare_semaphore:
            df = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq",
            )
        if df is None or df.empty:
            raise ValueError(f"{code}: no data")
        return df

    try:
        df = _fetch()
        df = df.rename(
            columns={
                "日期": "date",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
                "成交额": "amount",
                "振幅": "amplitude",
                "涨跌幅": "pct_change",
                "涨跌额": "change",
                "换手率": "turnover",
            }
        )
        df["date"] = pd.to_datetime(df["date"])
        return Ok(df.set_index("date").sort_index())
    except Exception as e:
        return Err(f"{code}: AKShare failed: {e}")


def _akshare_index_kline(
    code: str, start_date: str, end_date: str, retries: int = 2
) -> Result[pd.DataFrame, str]:
    """AKShare 获取指数 K 线（专用指数接口）。

    使用 stock_zh_index_daily 接口获取指数数据，支持上证、深证、中证等。
    指数代码格式：sh000300（沪深300）、sh000001（上证指数）、sz399001（深证成指）。
    """
    # 标准化指数代码
    symbol = code
    if not symbol.startswith(("sh", "sz")):
        # 上海指数以 0 开头，深圳以 3 开头
        if symbol.startswith("0"):
            symbol = f"sh{symbol}"
        elif symbol.startswith("3"):
            symbol = f"sz{symbol}"
        else:
            symbol = f"sh{symbol}"

    for attempt in range(retries + 1):
        try:
            with _akshare_semaphore:
                df = ak.stock_zh_index_daily(symbol=symbol)
            if df is None or df.empty:
                return Err(f"{code}: no index data from AKShare")
            # 标准化列名
            df = df.rename(
                columns={
                    "date": "date",
                    "open": "open",
                    "close": "close",
                    "high": "high",
                    "low": "low",
                    "volume": "volume",
                    "amount": "amount",
                }
            )
            # 过滤日期范围
            df["date"] = pd.to_datetime(df["date"])
            df = df[
                (df["date"] >= pd.Timestamp(start_date)) & (df["date"] <= pd.Timestamp(end_date))
            ]
            if df.empty:
                return Err(f"{code}: no data in date range {start_date}~{end_date}")
            df = df.set_index("date").sort_index()
            return Ok(df)
        except Exception as e:
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
            else:
                return Err(f"{code}: AKShare index failed after {retries + 1} attempts: {e}")
    return Err(f"{code}: unreachable")


def get_kline(code: str | int, days: int, cache: DataCache) -> Result[pd.DataFrame, str]:
    """获取历史 K 线。mootdx 优先（TCP 直连，仅个股），腾讯备用，AKShare 兜底，带缓存。

    指数代码（000300, 000001, 399001 等）直接走 AKShare 指数专用接口，
    避免 mootdx/腾讯/个股接口的多次失败重试。
    """
    code = str(code)  # 确保是字符串
    cached = cache.get(code)
    if cached is not None:
        # 修复：确保日期格式正确
        cached = fix_kline_dates(cached)
        # 检查缓存数据是否满足 days 要求（至少 80% 的 requested days）
        if len(cached) >= days * 0.8:
            return Ok(cached)
        # 缓存数据不足，重新获取
        logger.info(
            "Cache for %s has %d bars < %d requested, refetching",
            code,
            len(cached),
            days,
        )

    end_date = pendulum.now().format("YYYYMMDD")
    start_date = pendulum.now().subtract(days=days).format("YYYYMMDD")

    # 指数代码识别：精确匹配已知指数代码，避免误判个股
    # 上海指数：000001(上证), 000300(沪深300), 000905(中证500), 000016(上证50), 000852(中证1000)
    # 深圳指数：399001(深证成指), 399006(创业板指), 399005(中小板指), 399673(创业板50)
    # 其他：880003(中证500), sh000001, sz399001 等带前缀格式
    _INDEX_CODES = {
        "000001",
        "000016",
        "000300",
        "000852",
        "000905",
        "399001",
        "399005",
        "399006",
        "399673",
    }
    is_index = code in _INDEX_CODES or code.startswith(("sh", "sz"))

    # 1. 个股：mootdx（通达信 TCP 协议，无 HTTP 限流，速度最快）
    if not is_index:
        from aimoon.data.mootdx_source import mootdx_kline

        result = mootdx_kline(code, days)
        if result.is_ok():
            kline = result.unwrap()
            # 修复：确保日期格式正确
            kline = fix_kline_dates(kline)
            cache.put(code, kline)
            return Ok(kline)

        # 2. 个股备用：腾讯（HTTP，无需 token）
        logger.warning("mootdx failed for %s: %s, trying Tencent", code, result.error)  # type: ignore[union-attr]
        tencent_result = _tencent_kline(code, days)
        if tencent_result.is_ok():
            kline = tencent_result.unwrap()
            # 修复：确保日期格式正确
            kline = fix_kline_dates(kline)
            cache.put(code, kline)
            return Ok(kline)

        # 3. 个股兜底：AKShare 个股接口
        logger.warning("Tencent failed for %s: %s, trying AKShare", code, tencent_result.error)  # type: ignore[union-attr]
        akshare_result = _akshare_kline(code, start_date, end_date)
        if akshare_result.is_ok():
            kline = akshare_result.unwrap()
            # 修复：确保日期格式正确
            if "date" in kline.columns:
                try:
                    kline["date"] = pd.to_datetime(kline["date"])
                    kline = kline.set_index("date")
                except Exception:
                    logger.debug("Kline date parse failed for %s", code)
            cache.put(code, kline)
            return Ok(kline)

        return akshare_result

    # 指数：直接走 AKShare 指数专用接口（跳过 mootdx/腾讯/个股接口）
    logger.info("Fetching index data via AKShare index API for %s", code)
    index_result = _akshare_index_kline(code, start_date, end_date)
    if index_result.is_ok():
        kline = index_result.unwrap()
        kline = fix_kline_dates(kline)
        cache.put(code, kline)
        return Ok(kline)

    # 兜底：尝试通用 AKShare 接口
    logger.warning("AKShare index API failed for %s, trying generic API", code)
    akshare_result = _akshare_kline(code, start_date, end_date)
    if akshare_result.is_ok():
        kline = akshare_result.unwrap()
        if "date" in kline.columns:
            try:
                kline["date"] = pd.to_datetime(kline["date"])
                kline = kline.set_index("date")
            except Exception:
                logger.debug("Kline date parse failed for %s", code)
        cache.put(code, kline)
        return Ok(kline)

    return index_result
