"""全市场实时行情 — 东财 API"""

from __future__ import annotations

import math
import random
import time
from pathlib import Path

import httpx
import pandas as pd

from aimoon.config import Config
from aimoon.result import Err, Ok, Result

_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

_SPOT_CACHE_TTL = 300  # 5 minutes — real-time data must be fresh


def _em_get(
    url: str, params: dict, timeout: int = 15, max_retries: int = 3
) -> httpx.Response:
    last_exc = None
    for attempt in range(max_retries):
        try:
            r = httpx.get(
                url, params=params, headers=_DEFAULT_HEADERS, timeout=timeout
            )
            r.raise_for_status()
            return r
        except (httpx.HTTPError, ValueError) as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                time.sleep(1.0 * (2**attempt) + random.uniform(0.5, 1.5))
    raise last_exc  # type: ignore[misc]


def _em_fetch_all_pages(
    base_url: str, base_params: dict, timeout: int = 15
) -> pd.DataFrame:
    r = _em_get(base_url, base_params, timeout=timeout)
    data = r.json()
    inner = data.get("data")
    if not inner:
        return pd.DataFrame()
    diff = inner.get("diff", [])
    if not diff:
        return pd.DataFrame()
    per_page = len(diff)
    total = inner.get("total", 0)
    total_pages = math.ceil(total / per_page)
    if total_pages <= 1:
        return pd.DataFrame(diff)
    frames = [pd.DataFrame(diff)]
    for page in range(2, total_pages + 1):
        p = {**base_params, "pn": str(page)}
        r = _em_get(base_url, p, timeout=timeout)
        page_inner = r.json().get("data")
        if page_inner:
            frames.append(pd.DataFrame(page_inner.get("diff", [])))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def get_spot(
    cfg: Config, cache_dir: Path | None = None
) -> Result[pd.DataFrame, str]:
    """从东财获取全市场实时行情。磁盘缓存 5 分钟。"""
    cache_dir = cache_dir or Path(cfg.cache_dir)
    spot_file = cache_dir / "_spot.json"
    if spot_file.exists():
        age = time.time() - spot_file.stat().st_mtime
        if age < _SPOT_CACHE_TTL:
            try:
                df = pd.read_json(spot_file, orient="records", lines=True)
                return Ok(df)
            except Exception:
                pass
    try:
        url = "https://push2delay.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": "1",
            "pz": "10000",
            "po": "1",
            "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
            "fid": "f12",
            "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
            "fields": "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,f26,f37",
        }
        df = _em_fetch_all_pages(url, params)
        if df.empty:
            return Err("Empty spot data")
        df = df.rename(
            columns={
                "f12": "stock_code",
                "f14": "stock_name",
                "f2": "price",
                "f3": "pct_change",
                "f4": "change",
                "f5": "volume",
                "f6": "amount",
                "f7": "amplitude",
                "f8": "turnover",
                "f9": "pe",
                "f10": "volume_ratio",
                "f15": "high",
                "f16": "low",
                "f17": "open",
                "f18": "prev_close",
                "f20": "total_market_cap",
                "f21": "float_market_cap",
                "f23": "pb",
                "f24": "pct_60d",
                "f25": "pct_ytd",
                "f26": "listing_date",
                "f37": "roe",  # ROE TTM（加权）
            }
        )
        spot_file.parent.mkdir(parents=True, exist_ok=True)
        df.to_json(spot_file, orient="records", lines=True, force_ascii=False)
        return Ok(df)
    except Exception as e:
        return Err(f"Fetch spot data failed: {e}")


_RENAME_MAP = {
    "f12": "stock_code",
    "f14": "stock_name",
    "f2": "price",
    "f3": "pct_change",
    "f4": "change",
    "f5": "volume",
    "f6": "amount",
    "f7": "amplitude",
    "f8": "turnover",
    "f9": "pe",
    "f10": "volume_ratio",
    "f15": "high",
    "f16": "low",
    "f17": "open",
    "f18": "prev_close",
    "f20": "total_market_cap",
    "f21": "float_market_cap",
    "f23": "pb",
    "f24": "pct_60d",
    "f25": "pct_ytd",
    "f26": "listing_date",
    "f37": "roe",
}

_FIELDS = (
    "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,f26,f37"
)


def get_spot_for_codes(
    codes: set[str], cfg: Config, cache_dir: Path | None = None
) -> Result[pd.DataFrame, str]:
    """批量获取指定股票的实时行情（每批 500 只，约 1 秒/批）。"""
    cache_dir = cache_dir or Path(cfg.cache_dir)
    cache_file = cache_dir / "_spot_pool.json"
    if (
        cache_file.exists()
        and (time.time() - cache_file.stat().st_mtime) < _SPOT_CACHE_TTL
    ):
        try:
            df = pd.read_json(cache_file, orient="records", lines=True)
            # 优化：确保 stock_code 是字符串类型，与 codes 集合匹配
            if "stock_code" in df.columns:
                df["stock_code"] = df["stock_code"].astype(str)
            # 过滤指定股票
            filtered = df[df["stock_code"].isin(codes)].reset_index(drop=True)
            cached_codes = set(df["stock_code"].unique())
            if codes.issubset(cached_codes):
                return Ok(filtered)
        except Exception:
            pass
    try:
        code_list = sorted(str(c) for c in codes)
        frames: list[pd.DataFrame] = []
        for i in range(0, len(code_list), 500):
            batch = code_list[i : i + 500]
            secids = ",".join(f"{'1' if c.startswith('6') else '0'}.{c}" for c in batch)
            url = "https://push2delay.eastmoney.com/api/qt/ulist.np/get"
            params = {"fltt": "2", "invt": "2", "fields": _FIELDS, "secids": secids}
            r = _em_get(url, params, timeout=15)
            diff = r.json().get("data", {}).get("diff", [])
            if diff:
                frames.append(pd.DataFrame(diff))
        if not frames:
            return Err("Empty spot data for pool")
        df = pd.concat(frames, ignore_index=True)
        df = df.rename(columns=_RENAME_MAP)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        df.to_json(cache_file, orient="records", lines=True, force_ascii=False)
        return Ok(df)
    except Exception as e:
        return Err(f"Fetch spot data for pool failed: {e}")
