"""Sector filters (cached 30 min)."""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(".aimoon_cache")


def _cached(key: str, ttl: int, fetcher):
    """Disk cache. Empty results are not cached."""
    path = _CACHE_DIR / f"_{key}.json"
    if path.exists() and (time.time() - path.stat().st_mtime) < ttl:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    result = fetcher()
    if result is not None:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8")
    return result


def _fetch_all_sectors(top_pct: float) -> dict[str, str]:
    """Fetch sector constituent mapping. Direct eastmoney API."""
    from aimoon.data.spot import em_get

    try:
        url = "https://push2delay.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": "1",
            "pz": "1000",
            "po": "1",
            "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": "m:90+t:2",
            "fields": "f12,f14,f3",
        }
        r = em_get(url, params, timeout=15)
        data = r.json()
        diff = data.get("data", {}).get("diff", [])
        if not diff:
            return {}
        sectors = [{"code": d["f12"], "name": d["f14"], "chg": d["f3"]} for d in diff]
        sectors.sort(key=lambda x: x["chg"], reverse=True)
        n_top = min(10, max(1, int(len(sectors) * top_pct / 100)))
        top_sectors = sectors[:n_top]
        logger.info("Top sectors: %s", [s["name"] for s in top_sectors])

        def _fetch_one(sector_code: str, sector_name: str) -> dict[str, str]:
            try:
                cons_url = "https://push2delay.eastmoney.com/api/qt/clist/get"
                cons_params = {
                    "pn": "1",
                    "pz": "5000",
                    "po": "1",
                    "np": "1",
                    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                    "fltt": "2",
                    "invt": "2",
                    "fid": "f12",
                    "fs": f"b:{sector_code}",
                    "fields": "f12",
                }
                cr = em_get(cons_url, cons_params, timeout=15)
                cdiff = cr.json().get("data", {}).get("diff", [])
                return {str(d["f12"]): sector_name for d in cdiff}
            except Exception:
                return {}

        result: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = {ex.submit(_fetch_one, s["code"], s["name"]): s["name"] for s in top_sectors}
            for fut in as_completed(futures):
                result.update(fut.result())
        return result
    except Exception as e:
        logger.warning("Sector fetch failed: %s", e)
        return {}


def get_sector_context(df: pd.DataFrame, top_pct: float = 5.0) -> dict:
    """Build sector market context (for scoring). Cached 30 min."""
    try:
        sector_map: dict[str, str] = _cached(
            f"sectors_{top_pct}", 30 * 60, lambda: _fetch_all_sectors(top_pct)
        )
    except Exception as e:
        logger.warning("Sector context fetch failed: %s", e)
        return {}
    if not sector_map:
        return {}

    df_copy = df
    if "pct_60d" not in df_copy.columns:
        df_copy["pct_60d"] = 0.0
    df_copy["pct_60d"] = pd.to_numeric(df_copy["pct_60d"], errors="coerce").fillna(0)
    df_copy["sector"] = df_copy["stock_code"].map(sector_map)
    sector_returns = df_copy.dropna(subset=["sector"]).groupby("sector")["pct_60d"].mean().to_dict()

    sorted_sectors = sorted(sector_returns.items(), key=lambda x: x[1], reverse=True)
    n_top = max(1, int(len(sorted_sectors) * top_pct / 100))
    top_sectors = {n for n, _ in sorted_sectors[:n_top]}
    n_bottom = max(1, int(len(sorted_sectors) * 0.30))
    bottom_sectors = {n for n, _ in sorted_sectors[-n_bottom:]}
    threshold = df_copy["pct_60d"].quantile(1 - top_pct / 100)
    top_stocks = set(df_copy[df_copy["pct_60d"] >= threshold]["stock_code"].tolist())

    return {
        "sector_map": sector_map,
        "sector_returns": sector_returns,
        "top_sectors": top_sectors,
        "bottom_sectors": bottom_sectors,
        "top_stocks": top_stocks,
        "top_pct": top_pct,
    }


def filter_by_sectors(df: pd.DataFrame, top_pct: float = 5.0) -> tuple[pd.DataFrame, dict]:
    """Sector filter. Returns (filtered, market_context). Legacy compat."""
    ctx = get_sector_context(df, top_pct)
    sector_map = ctx.get("sector_map", {})
    if not sector_map:
        return df, ctx
    filtered = df[df["stock_code"].isin(set(sector_map.keys()))].reset_index(drop=True)
    return filtered, ctx
