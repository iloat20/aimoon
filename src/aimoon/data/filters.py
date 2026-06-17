"""data filters -- pure functions."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from aimoon.config import Config


def filter_universe(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Filter stock universe by market cap and board exclusions.

    This is the main entry point called by cli.py.  Applies all basic
    screening rules from Config on a spot-data DataFrame.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    result = df

    # Convert numeric columns
    for col in ("total_market_cap", "float_market_cap", "turnover", "price"):
        if col in result.columns:
            result[col] = pd.to_numeric(result[col], errors="coerce")

    # --- Market cap filter (config values are in 亿) ---
    if "total_market_cap" in result.columns:
        cap_yi = result["total_market_cap"] / 1e8  # raw is in 元, convert to 亿
        result = result[(cap_yi >= cfg.min_market_cap_yi) & (cap_yi < cfg.max_market_cap_yi)]

    # --- Turnover rate filter (skip when min=0 and max>=100) ---
    if "turnover" in result.columns and (cfg.min_turnover_pct > 0 or cfg.max_turnover_pct < 100):
        result = result[
            (result["turnover"] >= cfg.min_turnover_pct)
            & (result["turnover"] <= cfg.max_turnover_pct)
        ]

    # --- Price filter (skip when min=0 and max is very large) ---
    if "price" in result.columns and cfg.min_price > 0:
        result = result[(result["price"] >= cfg.min_price) & (result["price"] <= cfg.max_price)]

    # --- Exclude boards (ST, 退市, 北交所) ---
    if "stock_name" in result.columns and cfg.exclude_boards:
        for keyword in cfg.exclude_boards:
            result = result[~result["stock_name"].astype(str).str.contains(keyword, na=False)]

    # --- Exclude prefixes (8xx, 4xx = 北交所/三板) ---
    if "stock_code" in result.columns and cfg.exclude_prefixes:
        mask = pd.Series(False, index=result.index)
        for prefix in cfg.exclude_prefixes:
            mask = mask | result["stock_code"].astype(str).str.startswith(prefix)
        result = result[~mask]

    # --- Listing date filter ---
    if "listing_date" in result.columns and cfg.min_list_days > 0:
        cutoff = (date.today() - timedelta(days=cfg.min_list_days)).strftime("%Y%m%d")
        ld = pd.to_numeric(result["listing_date"], errors="coerce")
        result = result[ld.astype("Int64") <= int(cutoff)]

    # --- PB filter ---
    if "pb" in result.columns and cfg.max_pb > 0:
        result = result[result["pb"].apply(lambda x: pd.notna(x) and 0 < x <= cfg.max_pb)]

    # Drop rows with missing essential fields
    essential = [c for c in ("stock_code", "stock_name", "price") if c in result.columns]
    if essential:
        result = result.dropna(subset=essential)

    # Remove zero/negative prices
    if "price" in result.columns:
        result = result[result["price"] > 0]

    return result.reset_index(drop=True)


def pre_sort_universe(df: pd.DataFrame, max_count: int = 300) -> pd.DataFrame:
    """Pre-sort by 60d return + volume and cap at max_count.

    Used in full-market fallback to keep K-line download volume manageable.
    """
    if df.empty or len(df) <= max_count:
        return df
    df = df
    df["pct_60d"] = pd.to_numeric(df.get("pct_60d", 0), errors="coerce").fillna(0)
    df["turnover"] = pd.to_numeric(df.get("turnover", 0), errors="coerce").fillna(0)
    # Composite rank: higher 60d return + moderate turnover preferred
    rank_60d = df["pct_60d"].rank(ascending=False) * 0.6
    rank_turn = df["turnover"].rank(ascending=False) * 0.4
    df["_rank"] = rank_60d + rank_turn
    return df.nsmallest(max_count, "_rank").drop(columns=["_rank"]).reset_index(drop=True)
