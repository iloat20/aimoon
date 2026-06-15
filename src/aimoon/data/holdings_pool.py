"""Institutional holdings pool — quarterly data, persisted to disk."""
from __future__ import annotations

import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path

import akshare as ak
import pandas as pd
import requests

from aimoon.config import Config

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(".aimoon_cache")

# Holdings pool file -- persisted separately so it survives cache clears
# and is available immediately on first run after install.
_POOL_TTL = 90 * 86400  # 90 days (one quarter)


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


def get_holdings_pool(
    cfg: Config, *, force: bool = False, cache_dir: Path | None = None
) -> set[str]:
    """Get institutional holdings pool.

    Returns cached/persisted pool on failure so users always have stocks
    to screen.  The pool is refreshed at most once per quarter.
    Use force=True to bypass TTL and rebuild from network.
    """
    cache_dir = cache_dir or Path(cfg.cache_dir)
    pool_file = cache_dir / "_holdings_pool.json"

    # 1. Check in-memory / disk cache first (skip if force)
    if not force and pool_file.exists():
        age = time.time() - pool_file.stat().st_mtime
        if age < _POOL_TTL:
            try:
                data = json.loads(pool_file.read_text(encoding="utf-8"))
                if data:
                    logger.info("Using cached holdings pool (%d stocks)", len(data))
                    return data
            except Exception:
                pass

    # 2. Try to build from network
    result = _build_holdings_pool(cfg)
    if result:
        cache_dir.mkdir(parents=True, exist_ok=True)
        pool_file.write_text(json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8")
        logger.info("Persisted holdings pool: %d stocks", len(result))
        return result

    # 3. Network failed -- try stale disk cache (any age)
    if pool_file.exists():
        try:
            data = json.loads(pool_file.read_text(encoding="utf-8"))
            if data:
                logger.warning("Using stale holdings pool (%d stocks)", len(data))
                return data
        except Exception:
            pass

    # 4. Last resort: shipped fallback
    fallback = _load_shipped_pool()
    if fallback:
        logger.info("Using shipped fallback pool (%d stocks)", len(fallback))
        return fallback

    return set()


def _load_shipped_pool() -> set[str]:
    """Load the pool file shipped with the package (in data/ directory)."""
    shipped = Path(__file__).parent / "holdings_pool.json"
    if shipped.exists():
        try:
            data = json.loads(shipped.read_text(encoding="utf-8"))
            return set(data) if isinstance(data, list) else set()
        except Exception:
            pass
    return set()


def save_shipped_pool(pool: set[str]) -> None:
    """Persist the current pool as the shipped fallback.

    Called by maintainers after a successful refresh so that fresh
    installs have an up-to-date pool without needing network access
    to northbound/fund/ROE APIs.
    """
    target = Path(__file__).parent / "holdings_pool.json"
    target.write_text(json.dumps(sorted(pool), ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Saved shipped pool: %d stocks -> %s", len(pool), target)


def _build_holdings_pool(cfg: Config) -> set[str] | None:
    # 1. Northbound holdings
    nb = _get_northbound(cfg.min_northbound_cap)
    if not nb:
        return None  # do not cache empty
    logger.info("Northbound holdings: %d", len(nb))

    # 2. Listed >= 1 year
    listing_pass = _filter_by_listing(nb, cfg.min_list_days)
    logger.info("After listing filter: %d", len(listing_pass))

    # 3. ROE(TTM) > 10%
    roe_pass = _filter_by_roe(listing_pass, min_roe=10.0)
    logger.info("After ROE filter: %d", len(roe_pass))

    # 4. Fund holdings >= 5% of float shares
    fund_pass = _filter_by_fund_pct(roe_pass, cfg.min_fund_pct, cfg)
    logger.info("After fund %% filter: %d", len(fund_pass))

    # 5. PE(TTM) < max_pe_ttm
    pe_pass = _filter_by_pe(fund_pass, cfg.max_pe_ttm, cfg)
    logger.info("After PE filter: %d", len(pe_pass))

    # 6. Dividend yield >= min_dividend_yield
    div_pass = _filter_by_dividend_yield(pe_pass, cfg.min_dividend_yield)
    logger.info("After dividend yield filter: %d", len(div_pass))

    return div_pass if div_pass else None


def _filter_by_fund_pct(codes: set[str], min_pct: float, cfg: Config | None = None) -> set[str]:
    """Fund holdings >= min_pct% of float shares."""
    try:
        from aimoon.config import Config as _Config
        from aimoon.data.spot import get_spot
        spot_cfg = cfg if cfg is not None else _Config()
        spot_result = get_spot(spot_cfg)
        if spot_result.is_err():
            return codes
        spot = spot_result.unwrap()
        spot = spot[spot["stock_code"].isin(codes)]
        if spot.empty:
            return codes
        cap = spot[["stock_code", "float_market_cap", "price"]].copy()
        cap["float_market_cap"] = pd.to_numeric(cap["float_market_cap"], errors="coerce")
        cap["price"] = pd.to_numeric(cap["price"], errors="coerce")
        cap = cap.dropna(subset=["float_market_cap", "price"])
        cap = cap[cap["price"] > 0]
        cap["float_shares"] = cap["float_market_cap"] / cap["price"]

        today = date.today()
        quarters = [(12, 31), (9, 30), (6, 30), (3, 31)]
        report_date = next(
            (date(today.year, m, d).strftime("%Y%m%d")
             for m, d in quarters if date(today.year, m, d) <= today),
            f"{today.year - 1}1231",
        )
        df = ak.stock_report_fund_hold(symbol="基金持仓", date=report_date)
        if df is None or df.empty:
            return codes
        cols = df.columns.tolist()
        code_col = next((c for c in cols if "代码" in str(c)), None)
        shares_col = next((c for c in cols if "持股总数" in str(c) or "数量" in str(c)), None)
        if code_col is None or shares_col is None:
            return codes
        fund = df[[code_col, shares_col]].copy()
        fund.columns = ["stock_code", "held_shares"]
        fund["held_shares"] = pd.to_numeric(fund["held_shares"], errors="coerce").fillna(0)

        merged = cap[["stock_code", "float_shares"]].merge(
            fund, on="stock_code", how="left",
        )
        merged["held_shares"] = merged["held_shares"].fillna(0)
        merged["pct"] = merged["held_shares"] / merged["float_shares"] * 100
        return set(merged[merged["pct"] >= min_pct]["stock_code"].tolist()) & codes
    except Exception as e:
        logger.warning("Fund %% filter failed: %s", e)
        return codes


def _filter_by_pe(codes: set[str], max_pe: float, cfg: Config | None = None) -> set[str]:
    """Filter by PE(TTM) < max_pe. Uses spot data for PE values."""
    try:
        from aimoon.config import Config as _Config
        from aimoon.data.spot import get_spot
        spot_cfg = cfg if cfg is not None else _Config()
        spot_result = get_spot(spot_cfg)
        if spot_result.is_err():
            return codes
        spot = spot_result.unwrap()
        spot = spot[spot["stock_code"].isin(codes)]
        if spot.empty:
            return codes
        pe = pd.to_numeric(spot["pe"], errors="coerce")
        # Keep stocks with 0 < PE < max_pe (exclude negative/zero PE and NaN)
        mask = (pe > 0) & (pe < max_pe) & pe.notna()
        return set(spot.loc[mask, "stock_code"].astype(str).tolist()) & codes
    except Exception as e:
        logger.warning("PE filter failed: %s", e)
        return codes


def _filter_by_dividend_yield(codes: set[str], min_yield: float) -> set[str]:
    """Filter by dividend yield >= min_yield% from East Money datacenter API."""
    try:
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        all_rows: list[dict] = []
        for page in range(1, 200):
            params = {
                "sortColumns": "REPORTDATE,SECURITY_CODE",
                "sortTypes": "-1,1",
                "pageSize": "500",
                "pageNumber": str(page),
                "reportName": "RPT_LICO_FN_CPD",
                "columns": "SECURITY_CODE,ZXGXL,REPORTDATE",
                "source": "WEB",
                "client": "WEB",
            }
            r = requests.get(url, params=params, timeout=15,
                             headers={"User-Agent": "Mozilla/5.0"})
            rows = (r.json().get("result") or {}).get("data") or []
            if not rows:
                break
            all_rows.extend(rows)
            if rows[-1].get("REPORTDATE", "")[:10] < "2025-01-01":
                break

        if not all_rows:
            return codes

        df = pd.DataFrame(all_rows)
        df["REPORTDATE"] = df["REPORTDATE"].str[:10]
        df = df.sort_values("REPORTDATE").groupby("SECURITY_CODE").last().reset_index()
        df["ZXGXL"] = pd.to_numeric(df["ZXGXL"], errors="coerce")
        # Filter for our codes and min yield
        df = df[df["SECURITY_CODE"].isin(codes)]
        df = df[df["ZXGXL"] >= min_yield]
        return set(df["SECURITY_CODE"].astype(str).tolist())
    except Exception as e:
        logger.warning("Dividend yield filter failed: %s", e)
        return codes


def _filter_by_listing(codes: set[str], min_days: int) -> set[str]:
    """Filter by listing date from eastmoney spot data."""
    try:
        from aimoon.data.spot import FIELDS, em_get
        code_list = sorted(str(c) for c in codes)
        result: set[str] = set()
        cutoff = (date.today() - timedelta(days=min_days)).strftime("%Y%m%d")
        for i in range(0, len(code_list), 500):
            batch = code_list[i:i + 500]
            secids = ",".join(
                f'{"1" if c.startswith("6") else "0"}.{c}' for c in batch
            )
            url = "https://push2delay.eastmoney.com/api/qt/ulist.np/get"
            params = {"fltt": "2", "invt": "2", "fields": FIELDS, "secids": secids}
            r = em_get(url, params, timeout=15)
            diff = r.json().get("data", {}).get("diff", [])
            for d in diff:
                ld = str(d.get("f26", ""))
                if ld and ld <= cutoff:
                    code = str(d.get("f12", ""))
                    if code:
                        result.add(code)
        return result
    except Exception as e:
        logger.warning("Listing filter failed: %s", e)
        return codes


def _get_northbound(min_cap: float) -> set[str]:
    """Get northbound-held stocks with market cap >= min_cap (亿).

    Uses East Money datacenter API directly (akshare northbound APIs
    are frequently broken due to upstream changes).
    """
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    min_cap_raw = min_cap * 1e8
    all_codes: set[str] = set()

    for page in range(1, 20):  # max 20 pages x 500 = 10000 stocks
        params = {
            "sortColumns": "HOLD_MARKET_CAP",
            "sortTypes": "-1",
            "pageSize": "500",
            "pageNumber": str(page),
            "reportName": "RPT_MUTUAL_HOLDSTOCKNORTH_STA",
            "columns": "SECURITY_CODE,HOLD_MARKET_CAP",
            "source": "WEB",
            "client": "WEB",
        }
        try:
            r = requests.get(url, params=params, timeout=15,
                             headers={"User-Agent": "Mozilla/5.0"})
            data = r.json()
            rows = (data.get("result") or {}).get("data")
            if not rows:
                break
            for row in rows:
                cap = row.get("HOLD_MARKET_CAP") or 0
                if cap >= min_cap_raw:
                    all_codes.add(str(row["SECURITY_CODE"]))
                else:
                    # sorted desc by cap, so all remaining are below threshold
                    return all_codes
        except Exception as e:
            logger.warning("Northbound page %d failed: %s", page, e)
            break

    return all_codes


def _filter_by_roe(codes: set[str], min_roe: float = 10.0) -> set[str]:
    """Filter by annual ROE >= min_roe%.

    Uses the latest annual report (Q4) ROE as TTM proxy.
    Q1/Q2/Q3 quarterly ROE is not annualized and would unfairly exclude stocks.
    """
    try:
        today = date.today()
        # Use latest Q4 (annual report) -- if before April, use previous year's Q4
        q4_year = today.year if today.month >= 5 else today.year - 1
        q4_date = f"{q4_year}1231"

        df = ak.stock_yjbb_em(date=q4_date)
        if df is None or df.empty:
            return codes
        cols = df.columns.tolist()
        code_col = next((c for c in cols if "代码" in str(c)), None)
        roe_col = next(
            (c for c in cols if "净资产收益率" in str(c) or "roe" in str(c).lower()),
            None,
        )
        if code_col is None or roe_col is None:
            return codes
        df[roe_col] = pd.to_numeric(df[roe_col], errors="coerce")
        filtered = df[df[roe_col] >= min_roe]
        return set(filtered[code_col].astype(str).tolist()) & codes
    except Exception as e:
        logger.warning("ROE filter failed: %s", e)
        return codes
