"""Data fetch layer - AKShare wrapper"""
from __future__ import annotations
import logging
from datetime import date, timedelta
import akshare as ak
import pandas as pd
from aimoon.config import CONFIG
from aimoon.result import Result, Ok, Err
logger = logging.getLogger(__name__)


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


def get_history_kline(stock_code: str, days: int | None = None) -> Result[pd.DataFrame, str]:
    days = days or CONFIG.history_days
    end_date = date.today().strftime("%Y%m%d")
    start_date = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
    try:
        df = ak.stock_zh_a_hist(
            symbol=stock_code, period="daily",
            start_date=start_date, end_date=end_date, adjust="qfq",
        )
        if df is None or df.empty:
            return Err(f"{stock_code}: no data")
        df = df.rename(columns={
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "成交额": "amount", "振幅": "amplitude",
            "涨跌幅": "pct_change", "涨跌额": "change", "换手率": "turnover",
        })
        df["date"] = pd.to_datetime(df["date"])
        return Ok(df.set_index("date").sort_index())
    except Exception as e:
        return Err(f"{stock_code}: {e}")


def get_spot_data() -> Result[pd.DataFrame, str]:
    try:
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            return Err("Empty spot data")
        df = df.rename(columns={
            "代码": "stock_code", "名称": "stock_name", "最新价": "price",
            "涨跌幅": "pct_change", "涨跌额": "change", "成交量": "volume",
            "成交额": "amount", "振幅": "amplitude", "最高": "high",
            "最低": "low", "今开": "open", "昨收": "prev_close",
            "换手率": "turnover", "量比": "volume_ratio",
            "市盈率-动态": "pe", "市净率": "pb",
            "总市值": "total_market_cap", "流通市值": "float_market_cap",
            "60日涨跌幅": "pct_60d", "年初至今涨跌幅": "pct_ytd",
        })
        return Ok(df)
    except Exception as e:
        return Err(f"Fetch spot data failed: {e}")


def filter_by_spot(df: pd.DataFrame) -> pd.DataFrame:
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