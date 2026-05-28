"""Data fetch layer - East Money / Tencent / Sina wrappers"""
from __future__ import annotations

import logging
import math
import time
import random
from datetime import date, timedelta

import akshare as ak
import pandas as pd
import requests

from aimoon.cache.provider import DataCache
from aimoon.config import CONFIG
from aimoon.result import Err, Ok, Result

logger = logging.getLogger(__name__)

_cache: DataCache | None = None


def _get_cache() -> DataCache:
    global _cache
    if _cache is None:
        _cache = DataCache(ttl_hours=CONFIG.cache_ttl_hours)
    return _cache


_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

def _em_get(url, params, timeout=15, max_retries=3):
    last_exc = None
    for attempt in range(max_retries):
        try:
            r = requests.get(url, params=params, headers=_DEFAULT_HEADERS, timeout=timeout)
            r.raise_for_status()
            return r
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                delay = 1.0 * (2 ** attempt) + random.uniform(0.5, 1.5)
                time.sleep(delay)
    raise last_exc

def _em_fetch_all_pages(base_url, base_params, timeout=15):
    r = _em_get(base_url, base_params, timeout=timeout)
    data = r.json()
    per_page = len(data["data"]["diff"])
    total = data["data"]["total"]
    total_pages = math.ceil(total / per_page)
    frames = [pd.DataFrame(data["data"]["diff"])]
    for page in range(2, total_pages + 1):
        p = base_params.copy()
        p["pn"] = str(page)
        time.sleep(random.uniform(0.3, 0.8))
        r = _em_get(base_url, p, timeout=timeout)
        inner = r.json()
        frames.append(pd.DataFrame(inner["data"]["diff"]))
    df = pd.concat(frames, ignore_index=True)
    df["f3"] = pd.to_numeric(df["f3"], errors="coerce")
    df.sort_values(by=["f3"], ascending=False, inplace=True, ignore_index=True)
    df.reset_index(inplace=True)
    df["index"] = df["index"].astype(int) + 1
    return df


def get_stock_list() -> Result[pd.DataFrame, str]:
    try:
        df = ak.stock_info_a_code_name()
        if df is None or df.empty:
            return Err("Empty stock list")
        df = df.rename(columns={"code": "stock_code", "name": "stock_name"})
        return Ok(df)
    except Exception as e:
        logger.error("Fetch stock list failed: %s", e)
        return Err(f"Fetch stock list failed: {e}")


def filter_stock_list(df: pd.DataFrame) -> pd.DataFrame:
    mask = pd.Series(True, index=df.index)
    for prefix in CONFIG.exclude_prefixes:
        mask &= ~df["stock_code"].str.startswith(prefix)
    for board in CONFIG.exclude_boards:
        mask &= ~df["stock_name"].str.contains(board, na=False)
    return df[mask].reset_index(drop=True)



def _tencent_kline(stock_code, days):
    prefix = "sh" if stock_code.startswith("6") else "sz"
    secid = prefix + stock_code
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {"param": f"{secid},day,,,{days},qfq"}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        data = r.json()
        if data.get("code") != 0:
            return Err(f"{stock_code}: Tencent API error")
        if secid not in data.get("data", {}):
            return Err(f"{stock_code}: no Tencent data")
        inner = data["data"][secid]
        key = "day" if "day" in inner else "qfqday"
        klines = inner.get(key, [])
        if not klines:
            return Err(f"{stock_code}: empty Tencent data")
        rows = []
        for k in klines:
            rows.append({"date": k[0], "open": float(k[1]), "close": float(k[2]),
                "high": float(k[3]), "low": float(k[4]), "volume": float(k[5])})
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        df["amount"] = 0.0
        df["amplitude"] = 0.0
        df["pct_change"] = 0.0
        df["change"] = 0.0
        df["turnover"] = 0.0
        return Ok(df)
    except Exception as e:
        return Err(f"{stock_code}: Tencent fallback failed: {e}")
def get_history_kline(stock_code: str, days: int | None = None) -> Result[pd.DataFrame, str]:
    days = days or CONFIG.history_days

    cache = _get_cache()
    cached = cache.get(stock_code)
    if cached is not None:
        return Ok(cached)

    end_date = date.today().strftime("%Y%m%d")
    start_date = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
    try:
        df = ak.stock_zh_a_hist(
            symbol=stock_code, period="daily",
            start_date=start_date, end_date=end_date, adjust="qfq",
        )
        if df is None or df.empty:
            return Err(f"{stock_code}: no data")
        df = df.rename(columns={"日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "成交额": "amount", "振幅": "amplitude",
            "涨跌幅": "pct_change", "涨跌额": "change", "换手率": "turnover",
})
        df["date"] = pd.to_datetime(df["date"])
        result_df = df.set_index("date").sort_index()
        cache.put(stock_code, result_df)
        return Ok(result_df)
    except Exception as e:
        logger.warning("AKShare kline failed for %s: %s, trying Tencent", stock_code, e)
        result = _tencent_kline(stock_code, days)
        if result.is_ok():
            cache.put(stock_code, result.unwrap())
        return result


def get_spot_data() -> Result[pd.DataFrame, str]:
    try:
        url = "https://push2delay.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": "1", "pz": "100", "po": "1", "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2", "invt": "2", "fid": "f12",
            "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
            "fields": "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25",
        }
        temp_df = _em_fetch_all_pages(url, params)
        if temp_df is None or temp_df.empty:
            return Err("Empty spot data")

        temp_df = temp_df.rename(columns={
            "f12": "stock_code", "f14": "stock_name",
            "f2": "price", "f3": "pct_change",
            "f4": "change", "f5": "volume", "f6": "amount",
            "f7": "amplitude", "f8": "turnover",
            "f9": "pe", "f10": "volume_ratio",
            "f15": "high", "f16": "low",
            "f17": "open", "f18": "prev_close",
            "f20": "total_market_cap", "f21": "float_market_cap",
            "f23": "pb", "f24": "pct_60d", "f25": "pct_ytd",
        })
        return Ok(temp_df)
    except Exception as e:
        return Err(f"Fetch spot data failed: {e}")




def filter_by_spot(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    num_cols = ["price", "turnover", "total_market_cap", "float_market_cap", "pe", "pb"]
    for col in num_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["price", "turnover", "total_market_cap"])
    cap_yi = df["total_market_cap"] / 1e8
    mask = (
        (cap_yi >= CONFIG.min_market_cap_yi)
        & (cap_yi <= CONFIG.max_market_cap_yi)
        & (df["turnover"] >= CONFIG.min_turnover_pct)
        & (df["turnover"] <= CONFIG.max_turnover_pct)
        & (df["price"] >= CONFIG.min_price)
        & (df["price"] <= CONFIG.max_price)
    )
    return df[mask].reset_index(drop=True)
