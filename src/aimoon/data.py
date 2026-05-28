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


def get_sector_mapping() -> Result[dict[str, str], str]:
    """获取股票 -> 行业板块映射。返回 {stock_code: sector_name}。"""
    try:
        df = ak.stock_board_industry_name_em()
        if df is None or df.empty:
            return Err("Empty sector list")
        mapping: dict[str, str] = {}
        for _, row in df.iterrows():
            sector_name = row.get("板块名称") or row.get("name", "")
            if not sector_name:
                continue
            try:
                cons = ak.stock_board_industry_cons_em(symbol=sector_name)
                if cons is not None and not cons.empty:
                    code_col = "代码" if "代码" in cons.columns else "code"
                    for code in cons[code_col].tolist():
                        mapping[str(code)] = sector_name
            except Exception:
                continue
        if not mapping:
            return Err("No sector data fetched")
        return Ok(mapping)
    except Exception as e:
        return Err(f"Fetch sector mapping failed: {e}")


def get_sector_returns(sector_map: dict[str, str], spot_df: pd.DataFrame) -> dict[str, float]:
    """计算各板块近一个月平均涨幅。返回 {sector_name: avg_pct}。"""
    df = spot_df.copy()
    df["pct_30d"] = pd.to_numeric(df.get("pct_60d", 0), errors="coerce").fillna(0)
    df["sector"] = df["stock_code"].map(sector_map)
    df = df.dropna(subset=["sector"])
    if df.empty:
        return {}
    return df.groupby("sector")["pct_30d"].mean().to_dict()


def get_top_sectors(top_pct: float = 5.0) -> Result[list[tuple[str, float]], str]:
    """从东方财富获取行业板块涨幅排名，返回 Top N% 的板块名和涨跌幅。
    返回 [(板块名, 涨跌幅%), ...] 按涨幅降序。
    """
    try:
        df = ak.stock_board_industry_name_em()
        if df is None or df.empty:
            return Err("Empty sector board data")
        name_col = "板块名称" if "板块名称" in df.columns else "name"
        change_col = None
        for c in ["涨跌幅", "涨幅"]:
            if c in df.columns:
                change_col = c
                break
        if change_col is None:
            return Err("No change column found in sector data")
        df[change_col] = pd.to_numeric(df[change_col], errors="coerce")
        df = df.dropna(subset=[change_col])
        df = df.sort_values(change_col, ascending=False).reset_index(drop=True)
        n_top = max(1, int(len(df) * top_pct / 100))
        top = df.head(n_top)
        return Ok(list(zip(top[name_col].tolist(), top[change_col].tolist())))
    except Exception as e:
        return Err(f"Fetch sector ranking failed: {e}")


def get_sector_stocks(sector_names: list[str]) -> dict[str, str]:
    """获取多个板块的成分股。返回 {stock_code: sector_name}。"""
    mapping: dict[str, str] = {}
    for name in sector_names:
        try:
            cons = ak.stock_board_industry_cons_em(symbol=name)
            if cons is not None and not cons.empty:
                code_col = "代码" if "代码" in cons.columns else "code"
                for code in cons[code_col].tolist():
                    mapping[str(code)] = name
        except Exception as e:
            logger.warning("Failed to fetch constituents for sector %s: %s", name, e)
    return mapping


def compute_market_rankings(
    spot_df: pd.DataFrame,
    sector_map: dict[str, str],
    top_pct: float = 5.0,
) -> dict:
    """计算全市场排名，返回市场上下文字典。
    包含 sector_map, sector_returns, top_sectors, top_stocks, top_pct。
    """
    spot_df = spot_df.copy()
    for col in ["pct_60d", "pct_ytd"]:
        if col in spot_df.columns:
            spot_df[col] = pd.to_numeric(spot_df[col], errors="coerce")
    spot_df["pct_60d"] = spot_df["pct_60d"].fillna(0)

    # 板块涨幅排名
    sector_returns = get_sector_returns(sector_map, spot_df)
    if sector_returns:
        sorted_sectors = sorted(sector_returns.items(), key=lambda x: x[1], reverse=True)
        n_top = max(1, int(len(sorted_sectors) * top_pct / 100))
        top_sectors = {name for name, _ in sorted_sectors[:n_top]}
    else:
        top_sectors = set()

    # 全市场股票涨幅排名（近60日）
    if "pct_60d" in spot_df.columns and len(spot_df) > 0:
        threshold = spot_df["pct_60d"].quantile(1 - top_pct / 100)
        top_stocks = set(spot_df[spot_df["pct_60d"] >= threshold]["stock_code"].tolist())
    else:
        top_stocks = set()

    return {
        "sector_map": sector_map,
        "sector_returns": sector_returns,
        "top_sectors": top_sectors,
        "top_stocks": top_stocks,
        "top_pct": top_pct,
    }


def get_northbound_holdings(min_shares: int = 5_000_000) -> set[str]:
    """获取北向持股 >= min_shares 的股票代码集合。
    数据来源：东方财富 datacenter-web 季报数据。
    """
    all_codes: set[str] = set()
    try:
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        for page in range(1, 20):
            params = {
                "sortColumns": "HOLD_SHARES",
                "sortTypes": "-1",
                "pageSize": "500",
                "pageNumber": str(page),
                "reportName": "RPT_MUTUAL_HOLDSTOCKNORTH_STA",
                "columns": "SECURITY_CODE,HOLD_SHARES",
                "source": "WEB",
                "client": "WEB",
            }
            r = requests.get(url, params=params, timeout=15, headers=_DEFAULT_HEADERS)
            data = r.json()
            if not data.get("success") or not data.get("result") or not data["result"].get("data"):
                break
            items = data["result"]["data"]
            for item in items:
                code = str(item.get("SECURITY_CODE", ""))
                shares = float(item.get("HOLD_SHARES", 0) or 0)
                if shares >= min_shares:
                    all_codes.add(code)
                else:
                    return all_codes  # sorted desc, stop early
            if len(items) < 500:
                break
        return all_codes
    except Exception as e:
        logger.warning("Northbound holdings fetch failed: %s", e)
        return set()


def get_social_security_holdings(
    min_pct: float = 2.0, spot_df: pd.DataFrame | None = None,
) -> set[str]:
    """获取社保基金持股占流通股比例 >= min_pct% 的股票代码集合。
    需要 spot_df 提供流通市值和股价来计算流通股数。
    """
    today = date.today()
    quarters = [(12, 31), (9, 30), (6, 30), (3, 31)]
    report_date = None
    for m, d in quarters:
        candidate = date(today.year, m, d)
        if candidate <= today:
            report_date = candidate.strftime("%Y%m%d")
            break
    if report_date is None:
        report_date = f"{today.year - 1}1231"

    try:
        df = ak.stock_report_fund_hold(symbol="社保持仓", date=report_date)
        if df is None or df.empty:
            return set()
        cols = df.columns.tolist()
        code_col = cols[1]    # 股票代码
        shares_col = cols[4]  # 持股数量
        df = df[[code_col, shares_col]].copy()
        df.columns = ["stock_code", "held_shares"]
        df["held_shares"] = pd.to_numeric(df["held_shares"], errors="coerce").fillna(0)

        if spot_df is not None and not spot_df.empty:
            # 用流通市值和股价计算流通股数
            cap = spot_df[["stock_code", "float_market_cap", "price"]].copy()
            cap["float_market_cap"] = pd.to_numeric(cap["float_market_cap"], errors="coerce")
            cap["price"] = pd.to_numeric(cap["price"], errors="coerce")
            cap = cap.dropna(subset=["float_market_cap", "price"])
            cap = cap[cap["price"] > 0]
            cap["float_shares"] = cap["float_market_cap"] / cap["price"]
            df = df.merge(cap[["stock_code", "float_shares"]], on="stock_code", how="inner")
            df["pct"] = df["held_shares"] / df["float_shares"] * 100
            return set(df[df["pct"] >= min_pct]["stock_code"].tolist())
        else:
            # 无 spot_df 时无法计算流通股占比，返回所有社保持仓股票
            return set(df["stock_code"].tolist())
    except Exception as e:
        logger.warning("Social security holdings fetch failed: %s", e)
        return set()
