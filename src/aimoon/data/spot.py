"""全市场实时行情 — 东财 API"""
from __future__ import annotations

import math
import os
import pickle
import random
import time
from pathlib import Path

import pandas as pd
import requests

from aimoon.config import Config
from aimoon.result import Err, Ok, Result

_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

_SPOT_CACHE_FILE = Path(".aimoon_cache") / "_spot.pkl"
_SPOT_CACHE_TTL = 86400  # 1 天


def _em_get(url: str, params: dict, timeout: int = 15, max_retries: int = 3) -> requests.Response:
    last_exc = None
    for attempt in range(max_retries):
        try:
            r = requests.get(url, params=params, headers=_DEFAULT_HEADERS, timeout=timeout)
            r.raise_for_status()
            return r
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                time.sleep(1.0 * (2 ** attempt) + random.uniform(0.5, 1.5))
    raise last_exc  # type: ignore[misc]


def _em_fetch_all_pages(base_url: str, base_params: dict, timeout: int = 15) -> pd.DataFrame:
    r = _em_get(base_url, base_params, timeout=timeout)
    data = r.json()
    diff = data["data"]["diff"]
    if not diff:
        return pd.DataFrame()
    per_page = len(diff)
    total = data["data"]["total"]
    frames = [pd.DataFrame(diff)]
    for page in range(2, math.ceil(total / per_page) + 1):
        p = {**base_params, "pn": str(page)}
        time.sleep(random.uniform(0.1, 0.3))
        r = _em_get(base_url, p, timeout=timeout)
        frames.append(pd.DataFrame(r.json()["data"]["diff"]))
    return pd.concat(frames, ignore_index=True)


def get_spot(cfg: Config) -> Result[pd.DataFrame, str]:
    """从东财获取全市场实时行情。磁盘缓存 5 分钟。"""
    if _SPOT_CACHE_FILE.exists():
        age = time.time() - _SPOT_CACHE_FILE.stat().st_mtime
        if age < _SPOT_CACHE_TTL:
            try:
                return Ok(pickle.loads(_SPOT_CACHE_FILE.read_bytes()))
            except Exception:
                pass
    try:
        url = "https://push2delay.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": "1", "pz": "10000", "po": "1", "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2", "invt": "2", "fid": "f12",
            "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
            "fields": "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,f26",
        }
        df = _em_fetch_all_pages(url, params)
        if df.empty:
            return Err("Empty spot data")
        df = df.rename(columns={
            "f12": "stock_code", "f14": "stock_name",
            "f2": "price", "f3": "pct_change",
            "f4": "change", "f5": "volume", "f6": "amount",
            "f7": "amplitude", "f8": "turnover",
            "f9": "pe", "f10": "volume_ratio",
            "f15": "high", "f16": "low",
            "f17": "open", "f18": "prev_close",
            "f20": "total_market_cap", "f21": "float_market_cap",
            "f23": "pb", "f24": "pct_60d", "f25": "pct_ytd",
            "f26": "listing_date",
        })
        _SPOT_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SPOT_CACHE_FILE.write_bytes(pickle.dumps(df))
        return Ok(df)
    except Exception as e:
        return Err(f"Fetch spot data failed: {e}")


_RENAME_MAP = {
    "f12": "stock_code", "f14": "stock_name",
    "f2": "price", "f3": "pct_change",
    "f4": "change", "f5": "volume", "f6": "amount",
    "f7": "amplitude", "f8": "turnover",
    "f9": "pe", "f10": "volume_ratio",
    "f15": "high", "f16": "low",
    "f17": "open", "f18": "prev_close",
    "f20": "total_market_cap", "f21": "float_market_cap",
    "f23": "pb", "f24": "pct_60d", "f25": "pct_ytd",
    "f26": "listing_date",
}

_FIELDS = "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,f26"


def get_spot_for_codes(codes: set[str], cfg: Config) -> Result[pd.DataFrame, str]:
    """批量获取指定股票的实时行情（每批 500 只，约 1 秒/批）。"""
    cache_key = "_spot_pool.pkl"
    cache_file = Path(cfg.cache_dir) / cache_key
    if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < _SPOT_CACHE_TTL:
        try:
            df = pickle.loads(cache_file.read_bytes())
            return Ok(df[df["stock_code"].isin(codes)].reset_index(drop=True))
        except Exception:
            pass
    try:
        code_list = sorted(str(c) for c in codes)
        frames: list[pd.DataFrame] = []
        for i in range(0, len(code_list), 500):
            batch = code_list[i:i + 500]
            secids = ",".join(
                f'{"1" if c.startswith("6") else "0"}.{c}' for c in batch
            )
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
        cache_file.write_bytes(pickle.dumps(df))
        return Ok(df)
    except Exception as e:
        return Err(f"Fetch spot data for pool failed: {e}")
