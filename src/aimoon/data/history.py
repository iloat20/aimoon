"""历史 K 线 — AKShare + 腾讯备用"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import akshare as ak
import pandas as pd
import requests

from aimoon.cache import DataCache
from aimoon.result import Err, Ok, Result

logger = logging.getLogger(__name__)


def _tencent_kline(stock_code: str, days: int) -> Result[pd.DataFrame, str]:
    prefix = "sh" if stock_code.startswith("6") else "sz"
    secid = prefix + stock_code
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {"param": f"{secid},day,,,{days},qfq"}
    try:
        r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        data = r.json()
        if data.get("code") != 0:
            return Err(f"{stock_code}: Tencent API error")
        inner = data["data"].get(secid, {})
        key = "day" if "day" in inner else "qfqday"
        klines = inner.get(key, [])
        if not klines:
            return Err(f"{stock_code}: empty Tencent data")
        rows = [{"date": k[0], "open": float(k[1]), "close": float(k[2]),
                 "high": float(k[3]), "low": float(k[4]), "volume": float(k[5])} for k in klines]
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        for col in ("amount", "amplitude", "pct_change", "change", "turnover"):
            df[col] = 0.0
        return Ok(df)
    except Exception as e:
        return Err(f"{stock_code}: Tencent fallback failed: {e}")


def get_kline(code: str, days: int, cache: DataCache) -> Result[pd.DataFrame, str]:
    """获取历史 K 线。AKShare 优先，腾讯备用，带缓存。"""
    cached = cache.get(code)
    if cached is not None:
        return Ok(cached)
    end_date = date.today().strftime("%Y%m%d")
    start_date = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
    try:
        df = ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=start_date, end_date=end_date, adjust="qfq",
        )
        if df is None or df.empty:
            return Err(f"{code}: no data")
        df = df.rename(columns={
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "成交额": "amount", "振幅": "amplitude",
            "涨跌幅": "pct_change", "涨跌额": "change", "换手率": "turnover",
        })
        df["date"] = pd.to_datetime(df["date"])
        result_df = df.set_index("date").sort_index()
        cache.put(code, result_df)
        return Ok(result_df)
    except Exception as e:
        logger.warning("AKShare kline failed for %s: %s, trying Tencent", code, e)
        result = _tencent_kline(code, days)
        if result.is_ok():
            cache.put(code, result.unwrap())
        return result
