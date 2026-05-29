"""数据过滤 — 纯函数"""
from __future__ import annotations

import logging
import pickle
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import akshare as ak
import pandas as pd
import requests

from aimoon.config import Config
from aimoon.data.spot import _DEFAULT_HEADERS

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(".aimoon_cache")


def _cached(key: str, ttl: int, fetcher):
    """磁盘缓存。空结果不缓存。"""
    path = _CACHE_DIR / f"_{key}.pkl"
    if path.exists() and (time.time() - path.stat().st_mtime) < ttl:
        try:
            return pickle.loads(path.read_bytes())
        except Exception:
            pass
    result = fetcher()
    if result is not None:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pickle.dumps(result))
    return result


# ---------------------------------------------------------------------------
# 持仓池（季度数据，缓存 90 天）
# ---------------------------------------------------------------------------

def get_holdings_pool(cfg: Config) -> set[str]:
    """获取机构持仓股票池。条件：北向≥1亿 + 基金占流通股≥5% + 上市满1年 + ROE>10%。
    季度报告数据，缓存 90 天。"""
    return _cached("holdings_pool", 90 * 86400, lambda: _build_holdings_pool(cfg))


def _build_holdings_pool(cfg: Config) -> set[str]:
    # 1. 北向持仓
    nb = _get_northbound(cfg.min_northbound_cap)
    if not nb:
        return set()
    logger.info("Northbound holdings: %d", len(nb))

    # 2. 上市满一年
    listing_pass = _filter_by_listing(nb, cfg.min_list_days)
    logger.info("After listing filter: %d", len(listing_pass))

    # 3. ROE(TTM) > 10%
    roe_pass = _filter_by_roe(listing_pass, min_roe=10.0)
    logger.info("After ROE filter: %d", len(roe_pass))

    # 4. 基金持股占流通股 >= 5%（需要行情数据计算流通股数）
    fund_pass = _filter_by_fund_pct(roe_pass, cfg.min_fund_pct)
    logger.info("After fund %% filter: %d", len(fund_pass))

    return fund_pass


def _filter_by_fund_pct(codes: set[str], min_pct: float) -> set[str]:
    """基金持股占流通股 >= min_pct%。获取行情数据计算流通股数。"""
    try:
        # 获取行情（用于计算流通股数）
        from aimoon.data.spot import get_spot_for_codes, Config
        spot_result = get_spot_for_codes(codes, Config())
        if spot_result.is_err():
            return codes
        spot = spot_result.unwrap()
        cap = spot[["stock_code", "float_market_cap", "price"]].copy()
        cap["float_market_cap"] = pd.to_numeric(cap["float_market_cap"], errors="coerce")
        cap["price"] = pd.to_numeric(cap["price"], errors="coerce")
        cap = cap.dropna(subset=["float_market_cap", "price"])
        cap = cap[cap["price"] > 0]
        cap["float_shares"] = cap["float_market_cap"] / cap["price"]

        # 获取基金持仓
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

        merged = fund.merge(cap[["stock_code", "float_shares"]], on="stock_code", how="inner")
        merged["pct"] = merged["held_shares"] / merged["float_shares"] * 100
        return set(merged[merged["pct"] >= min_pct]["stock_code"].tolist())
    except Exception as e:
        logger.warning("Fund %% filter failed: %s", e)
        return codes


def _filter_by_listing(codes: set[str], min_days: int) -> set[str]:
    """根据东财行情数据中的上市日期过滤。"""
    try:
        from aimoon.data.spot import _em_get, _FIELDS
        code_list = sorted(str(c) for c in codes)
        result: set[str] = set()
        cutoff = (date.today() - timedelta(days=min_days)).strftime("%Y%m%d")
        for i in range(0, len(code_list), 500):
            batch = code_list[i:i + 500]
            secids = ",".join(
                f'{"1" if c.startswith("6") else "0"}.{c}' for c in batch
            )
            url = "https://push2delay.eastmoney.com/api/qt/ulist.np/get"
            params = {"fltt": "2", "invt": "2", "fields": "f12,f26", "secids": secids}
            r = _em_get(url, params, timeout=15)
            diff = r.json().get("data", {}).get("diff", [])
            for item in diff:
                code = str(item.get("f12", ""))
                ld = item.get("f26")
                if ld and str(int(ld)) <= cutoff:
                    result.add(code)
        return result
    except Exception as e:
        logger.warning("Listing filter failed: %s", e)
        return codes


def _filter_by_roe(codes: set[str], min_roe: float = 10.0) -> set[str]:
    """根据东财数据中心的加权平均 ROE 过滤（取最新年报）。"""
    try:
        # 最新完整年报：如果当前月份 < 6，用上上年；否则用上年
        current_year = date.today().year
        report_year = current_year - 1 if date.today().month >= 6 else current_year - 2
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        roe_pass: set[str] = set()
        page = 1
        while True:
            params = {
                "sortColumns": "SECURITY_CODE", "sortTypes": "1",
                "pageSize": "500", "pageNumber": str(page),
                "reportName": "RPT_LICO_FN_CPD",
                "columns": "SECURITY_CODE,WEIGHTAVG_ROE",
                "filter": '(DATEMMDD="年报")(DATAYEAR="%d")(WEIGHTAVG_ROE>%s)' % (report_year, min_roe),
                "source": "WEB", "client": "WEB",
            }
            r = requests.get(url, params=params, timeout=15, headers=_DEFAULT_HEADERS)
            data = r.json()
            if not data.get("success") or not data.get("result") or not data["result"].get("data"):
                break
            items = data["result"]["data"]
            for item in items:
                code = str(item.get("SECURITY_CODE", ""))
                if code not in codes:
                    continue
                roe = item.get("WEIGHTAVG_ROE")
                if roe is not None and float(roe) > min_roe:
                    roe_pass.add(code)
            if len(items) < 500:
                break
            page += 1
        return roe_pass
    except Exception as e:
        logger.warning("ROE filter failed: %s", e)
        return codes


def _get_northbound(min_cap_yi: float) -> set[str]:
    codes: set[str] = set()
    min_cap = min_cap_yi * 1e8
    try:
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        page = 1
        while True:
            params = {
                "sortColumns": "HOLD_MARKET_CAP", "sortTypes": "-1",
                "pageSize": "500", "pageNumber": str(page),
                "reportName": "RPT_MUTUAL_HOLDSTOCKNORTH_STA",
                "columns": "SECURITY_CODE,HOLD_MARKET_CAP",
                "source": "WEB", "client": "WEB",
            }
            r = requests.get(url, params=params, timeout=15, headers=_DEFAULT_HEADERS)
            data = r.json()
            if not data.get("success") or not data.get("result") or not data["result"].get("data"):
                break
            items = data["result"]["data"]
            for item in items:
                cap = float(item.get("HOLD_MARKET_CAP", 0) or 0)
                if cap >= min_cap:
                    codes.add(str(item.get("SECURITY_CODE", "")))
            if len(items) < 500:
                break
            page += 1
    except Exception as e:
        logger.warning("Northbound holdings fetch failed: %s", e)
    return codes


# ---------------------------------------------------------------------------
# 基础过滤
# ---------------------------------------------------------------------------

def filter_universe(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """基础过滤：市值、换手率、价格、上市日期、排除规则。"""
    df = df.copy()
    for col in ("price", "turnover", "total_market_cap", "float_market_cap", "pe", "pb"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["price", "turnover", "total_market_cap"])
    cap_yi = df["total_market_cap"] / 1e8
    mask = (
        (cap_yi >= cfg.min_market_cap_yi) & (cap_yi <= cfg.max_market_cap_yi)
        & (df["turnover"] >= cfg.min_turnover_pct) & (df["turnover"] <= cfg.max_turnover_pct)
        & (df["price"] >= cfg.min_price) & (df["price"] <= cfg.max_price)
    )
    if "listing_date" in df.columns:
        cutoff = (date.today() - timedelta(days=cfg.min_list_days)).strftime("%Y%m%d")
        ld = pd.to_numeric(df["listing_date"], errors="coerce")
        mask = mask & ld.notna() & (ld.astype(int).astype(str) <= cutoff)
    for prefix in cfg.exclude_prefixes:
        mask &= ~df["stock_code"].str.startswith(prefix)
    for board in cfg.exclude_boards:
        mask &= ~df["stock_name"].str.contains(board, na=False)
    return df[mask].reset_index(drop=True)


def filter_by_fund_pct(spot: pd.DataFrame, min_pct: float = 5.0) -> set[str]:
    """基金持股占流通股 >= min_pct% 的股票代码。缓存 90 天。"""
    return _cached("fund_pct", 90 * 86400, lambda: _calc_fund_pct(spot, min_pct))


def _calc_fund_pct(spot: pd.DataFrame, min_pct: float) -> set[str]:
    try:
        today = date.today()
        quarters = [(12, 31), (9, 30), (6, 30), (3, 31)]
        report_date = next(
            (date(today.year, m, d).strftime("%Y%m%d")
             for m, d in quarters if date(today.year, m, d) <= today),
            f"{today.year - 1}1231",
        )
        df = ak.stock_report_fund_hold(symbol="基金持仓", date=report_date)
        if df is None or df.empty:
            return set()
        cols = df.columns.tolist()
        code_col = next((c for c in cols if "代码" in str(c)), None)
        shares_col = next((c for c in cols if "持股总数" in str(c) or "数量" in str(c)), None)
        if code_col is None or shares_col is None:
            return set()
        fund = df[[code_col, shares_col]].copy()
        fund.columns = ["stock_code", "held_shares"]
        fund["held_shares"] = pd.to_numeric(fund["held_shares"], errors="coerce").fillna(0)

        cap = spot[["stock_code", "float_market_cap", "price"]].copy()
        cap["float_market_cap"] = pd.to_numeric(cap["float_market_cap"], errors="coerce")
        cap["price"] = pd.to_numeric(cap["price"], errors="coerce")
        cap = cap.dropna(subset=["float_market_cap", "price"])
        cap = cap[cap["price"] > 0]
        cap["float_shares"] = cap["float_market_cap"] / cap["price"]

        merged = fund.merge(cap[["stock_code", "float_shares"]], on="stock_code", how="inner")
        merged["pct"] = merged["held_shares"] / merged["float_shares"] * 100
        result = set(merged[merged["pct"] >= min_pct]["stock_code"].tolist())
        logger.info("Fund holdings >= %.0f%%: %d stocks", min_pct, len(result))
        return result
    except Exception as e:
        logger.warning("Fund holdings filter failed: %s", e)
        return set()


# ---------------------------------------------------------------------------
# 板块过滤（缓存 30 分钟）
# ---------------------------------------------------------------------------

def _fetch_all_sectors(top_pct: float) -> dict[str, str]:
    """获取板块成分股映射。直接调用东财 API（绕过 AKShare）。"""
    from aimoon.data.spot import _em_get, _em_fetch_all_pages, _RENAME_MAP
    try:
        # 获取行业板块列表
        url = "https://push2delay.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": "1", "pz": "1000", "po": "1", "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2", "invt": "2", "fid": "f3",
            "fs": "m:90+t:2",
            "fields": "f12,f14,f3",
        }
        r = _em_get(url, params, timeout=15)
        data = r.json()
        diff = data.get("data", {}).get("diff", [])
        if not diff:
            return {}
        sectors = [{"code": d["f12"], "name": d["f14"], "chg": d["f3"]} for d in diff]
        sectors.sort(key=lambda x: x["chg"], reverse=True)
        n_top = min(10, max(1, int(len(sectors) * top_pct / 100)))
        top_sectors = sectors[:n_top]
        logger.info("Top sectors: %s", [s["name"] for s in top_sectors])

        # 获取成分股
        def _fetch_one(sector_code: str, sector_name: str) -> dict[str, str]:
            try:
                cons_url = "https://push2delay.eastmoney.com/api/qt/clist/get"
                cons_params = {
                    "pn": "1", "pz": "5000", "po": "1", "np": "1",
                    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                    "fltt": "2", "invt": "2", "fid": "f12",
                    "fs": f"b:{sector_code}",
                    "fields": "f12",
                }
                cr = _em_get(cons_url, cons_params, timeout=15)
                cdiff = cr.json().get("data", {}).get("diff", [])
                return {str(d["f12"]): sector_name for d in cdiff}
            except Exception:
                return {}

        result: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = {
                ex.submit(_fetch_one, s["code"], s["name"]): s["name"]
                for s in top_sectors
            }
            for fut in as_completed(futures):
                result.update(fut.result())
        return result
    except Exception as e:
        logger.warning("Sector fetch failed: %s", e)
        return {}


def get_sector_context(df: pd.DataFrame, top_pct: float = 5.0) -> dict:
    """构建板块市场上下文（用于评分）。板块数据缓存 30 分钟。"""
    try:
        sector_map: dict[str, str] = _cached(f"sectors_{top_pct}", 30 * 60, lambda: _fetch_all_sectors(top_pct))
    except Exception as e:
        logger.warning("Sector context fetch failed: %s", e)
        return {}
    if not sector_map:
        return {}

    df_copy = df.copy()
    if "pct_60d" not in df_copy.columns:
        df_copy["pct_60d"] = 0.0
    df_copy["pct_60d"] = pd.to_numeric(df_copy["pct_60d"], errors="coerce").fillna(0)
    df_copy["sector"] = df_copy["stock_code"].map(sector_map)
    sector_returns = df_copy.dropna(subset=["sector"]).groupby("sector")["pct_60d"].mean().to_dict()

    sorted_sectors = sorted(sector_returns.items(), key=lambda x: x[1], reverse=True)
    n_top = max(1, int(len(sorted_sectors) * top_pct / 100))
    top_sectors = {n for n, _ in sorted_sectors[:n_top]}
    threshold = df_copy["pct_60d"].quantile(1 - top_pct / 100)
    top_stocks = set(df_copy[df_copy["pct_60d"] >= threshold]["stock_code"].tolist())

    return {
        "sector_map": sector_map,
        "sector_returns": sector_returns,
        "top_sectors": top_sectors,
        "top_stocks": top_stocks,
        "top_pct": top_pct,
    }


def filter_by_sectors(df: pd.DataFrame, top_pct: float = 5.0) -> tuple[pd.DataFrame, dict]:
    """板块过滤。返回 (filtered, market_context)。兼容旧接口。"""
    ctx = get_sector_context(df, top_pct)
    sector_map = ctx.get("sector_map", {})
    if not sector_map:
        return df, ctx
    filtered = df[df["stock_code"].isin(set(sector_map.keys()))].reset_index(drop=True)
    return filtered, ctx
