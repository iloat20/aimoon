"""Akshare financial data adapter — fetches structured financial statements.

Replaces PysnowballAdapter with direct akshare API calls:
- No token required
- Structured numeric data (no PDF parsing)
- Built-in YoY ratios
- Historical data (100+ reports)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import akshare as ak
import pandas as pd

from aimoon.adapters.driven.common.cache import DiskTtlCache
from aimoon.core.domain.entities.financial import FinancialData, QuarterlyFinancialData

logger = logging.getLogger(__name__)


def _safe_float(value: Any) -> float:
    """Safely convert a value to float, returning 0.0 on failure."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _get_col(df: pd.DataFrame, col_name: str) -> Any:
    """Get a column value from the first row, returning None if column doesn't exist."""
    if col_name not in df.columns:
        return None
    val = df.iloc[0][col_name]
    if pd.isna(val):
        return None
    return val


def _filter_report_type(df: pd.DataFrame, report_type: str) -> pd.DataFrame:
    """Filter DataFrame to only include rows of the given report type."""
    if "REPORT_TYPE" not in df.columns:
        return df
    filtered = df[df["REPORT_TYPE"] == report_type]
    return filtered if not filtered.empty else df


class AkshareFinancialAdapter:
    """Fetches structured financial data from akshare (东方财富).

    Provides the same interface as PysnowballAdapter but uses akshare's
    East Money APIs for reliable, token-free data access.
    """

    def __init__(self) -> None:
        self._cache = DiskTtlCache(
            namespace="akshare_financial",
            ttl_seconds=86400,  # 24 hours
        )

    async def fetch(self, symbol: str, **kwargs: Any) -> FinancialData:
        """Fetch financial data for a symbol.

        Defaults to annual report (年报). Pass report_type="中报" for semi-annual,
        "一季报" or "三季报" for quarterly.
        """
        report_type = kwargs.get("report_type", "年报")
        cache_key = f"financial:{symbol}:{report_type}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            if isinstance(cached, dict) and cached.get("_empty"):
                return FinancialData(symbol=symbol, source="akshare_cache_empty")
            return FinancialData.model_validate(cached)

        try:
            result = await asyncio.to_thread(self._fetch_all, symbol, report_type)
        except Exception as e:
            logger.warning("[akshare] fetch failed for %s: %s", symbol, e)
            return FinancialData(symbol=symbol, source=f"akshare_failed: {e}")

        if result.source.startswith("akshare_empty"):
            self._cache.set(cache_key, {"_empty": True})
        else:
            self._cache.set(cache_key, result.model_dump())

        return result

    def _fetch_all(self, symbol: str, report_type: str = "年报") -> FinancialData:
        """Fetch and merge data from all three financial statements."""
        result = FinancialData(symbol=symbol, source="akshare(东方财富)")

        # Determine akshare symbol prefix
        prefix = "SH" if symbol.startswith("6") else "SZ" if symbol.startswith("0") else "BJ"
        ak_symbol = f"{prefix}{symbol}"

        # Fetch profit sheet (income statement)
        try:
            income_df = ak.stock_profit_sheet_by_report_em(symbol=ak_symbol)
            if income_df is not None and not income_df.empty:
                income_df = _filter_report_type(income_df, report_type)
                self._parse_income_statement(result, income_df)
        except Exception as e:
            logger.debug("[akshare] income statement failed: %s", e)

        # Fetch balance sheet
        try:
            bs_df = ak.stock_balance_sheet_by_report_em(symbol=ak_symbol)
            if bs_df is not None and not bs_df.empty:
                bs_df = _filter_report_type(bs_df, report_type)
                self._parse_balance_sheet(result, bs_df)
        except Exception as e:
            logger.debug("[akshare] balance sheet failed: %s", e)

        # Fetch cash flow statement
        try:
            cf_df = ak.stock_cash_flow_sheet_by_report_em(symbol=ak_symbol)
            if cf_df is not None and not cf_df.empty:
                cf_df = _filter_report_type(cf_df, report_type)
                self._parse_cash_flow(result, cf_df)
        except Exception as e:
            logger.debug("[akshare] cash flow failed: %s", e)

        # Check if we got any meaningful data
        if result.revenue == 0 and result.net_profit == 0 and result.total_assets == 0:
            result.source = "akshare_empty"

        # Calculate ROE = net_profit / equity
        if result.net_profit != 0 and result.equity > 0:
            result.roe = round(result.net_profit / result.equity * 100, 2)

        return result

    @staticmethod
    def _filter_by_report_type(df: pd.DataFrame, report_type: str) -> pd.DataFrame:
        """Filter DataFrame to only include rows of the given report type."""
        if "REPORT_TYPE" not in df.columns:
            return df
        filtered = df[df["REPORT_TYPE"] == report_type]
        if filtered.empty:
            logger.debug("[akshare] no data for report_type=%s, using latest", report_type)
            return df
        return filtered

    def _parse_income_statement(self, result: FinancialData, df: pd.DataFrame) -> None:
        """Parse income statement DataFrame into FinancialData."""
        result.report_period = str(_get_col(df, "REPORT_DATE"))[:10]

        revenue = _safe_float(_get_col(df, "TOTAL_OPERATE_INCOME"))
        if revenue > 0:
            result.revenue = revenue

        revenue_yoy = _safe_float(_get_col(df, "TOTAL_OPERATE_INCOME_YOY"))
        if revenue_yoy != 0:
            result.revenue_yoy = revenue_yoy

        net_profit = _safe_float(_get_col(df, "NETPROFIT"))
        if net_profit != 0:
            result.net_profit = net_profit

        net_profit_yoy = _safe_float(_get_col(df, "NETPROFIT_YOY"))
        if net_profit_yoy != 0:
            result.net_profit_yoy = net_profit_yoy

        eps = _safe_float(_get_col(df, "BASIC_EPS"))
        if eps > 0:
            result.eps = eps

    def _parse_balance_sheet(self, result: FinancialData, df: pd.DataFrame) -> None:
        """Parse balance sheet DataFrame into FinancialData."""
        total_assets = _safe_float(_get_col(df, "TOTAL_ASSETS"))
        if total_assets > 0:
            result.total_assets = total_assets

        total_liab = _safe_float(_get_col(df, "TOTAL_LIABILITIES"))
        if total_liab > 0:
            result.total_liabilities = total_liab

        if result.total_assets > 0 and result.total_liabilities > 0:
            result.equity = result.total_assets - result.total_liabilities

    def _parse_cash_flow(self, result: FinancialData, df: pd.DataFrame) -> None:
        """Parse cash flow statement DataFrame into FinancialData."""
        operating_cf = _safe_float(_get_col(df, "NETCASH_OPERATE"))
        if operating_cf != 0:
            result.operating_cf = operating_cf

        investing_cf = _safe_float(_get_col(df, "NETCASH_INVEST"))
        if investing_cf != 0:
            result.investing_cf = investing_cf

        financing_cf = _safe_float(_get_col(df, "NETCASH_FINANCE"))
        if financing_cf != 0:
            result.financing_cf = financing_cf

    async def fetch_financial(self, symbol: str, **kwargs: Any) -> FinancialData:
        """Alias for fetch() — matches PysnowballAdapter interface."""
        return await self.fetch(symbol)

    async def fetch_quarterly(self, symbol: str) -> QuarterlyFinancialData:
        """Fetch the latest non-annual report data.

        Returns the most recent 一季报/中报/三季报, preferring the latest date.
        """
        prefix = "SH" if symbol.startswith("6") else "SZ" if symbol.startswith("0") else "BJ"
        ak_symbol = f"{prefix}{symbol}"

        try:
            df = await asyncio.to_thread(ak.stock_profit_sheet_by_report_em, ak_symbol)
        except Exception as e:
            logger.debug("[akshare] fetch_quarterly failed: %s", e)
            return QuarterlyFinancialData(symbol=symbol, source="akshare_quarterly_failed")

        if df is None or df.empty:
            return QuarterlyFinancialData(symbol=symbol, source="akshare_quarterly_empty")

        # Filter out annual reports, sort by date descending, take the latest
        non_annual = df[df["REPORT_TYPE"] != "年报"] if "REPORT_TYPE" in df.columns else df
        if non_annual.empty:
            return QuarterlyFinancialData(symbol=symbol, source="akshare_quarterly_empty")

        # Get the most recent non-annual report
        latest = non_annual.iloc[0]
        if "REPORT_TYPE" in non_annual.columns:
            report_type = latest.get("REPORT_TYPE", "一季报")
        else:
            report_type = "一季报"
        return self._parse_quarterly(non_annual, symbol, report_type)

    @staticmethod
    def _parse_quarterly(df: pd.DataFrame, symbol: str, report_type: str) -> QuarterlyFinancialData:
        """Parse quarterly DataFrame into QuarterlyFinancialData."""
        result = QuarterlyFinancialData(
            symbol=symbol,
            report_period=str(_get_col(df, "REPORT_DATE"))[:10],
            report_type=report_type,
            source="akshare(东方财富)",
        )

        revenue = _safe_float(_get_col(df, "TOTAL_OPERATE_INCOME"))
        if revenue > 0:
            result.revenue = revenue

        revenue_yoy = _safe_float(_get_col(df, "TOTAL_OPERATE_INCOME_YOY"))
        if revenue_yoy != 0:
            result.revenue_yoy = revenue_yoy

        net_profit = _safe_float(_get_col(df, "NETPROFIT"))
        if net_profit != 0:
            result.net_profit = net_profit

        net_profit_yoy = _safe_float(_get_col(df, "NETPROFIT_YOY"))
        if net_profit_yoy != 0:
            result.net_profit_yoy = net_profit_yoy

        return result

    async def fetch_capital_flow(self, symbol: str, **kwargs: Any) -> dict:
        """Fetch individual stock capital flow data.

        Returns dict with:
            main_net_5d: 5-day main capital net inflow (in yuan)
            main_net_3d: 3-day main capital net inflow
            main_net_10d: 10-day main capital net inflow
            main_net_20d: 20-day main capital net inflow
            recent_date: date of the latest data
        """
        market = "sh" if symbol.startswith("6") or symbol.startswith("0") else "bj"
        try:
            df = await asyncio.to_thread(ak.stock_individual_fund_flow, stock=symbol, market=market)
            if df is None or df.empty:
                return {}
            # Data is sorted desc by date; columns include 主力净流入-净额
            row_count = len(df)
            main_col = "主力净流入-净额"
            result: dict[str, Any] = {"recent_date": str(df.iloc[0]["日期"])}
            if row_count >= 5:
                result["main_net_5d"] = float(df.head(5)[main_col].sum())
            if row_count >= 3:
                result["main_net_3d"] = float(df.head(3)[main_col].sum())
            if row_count >= 10:
                result["main_net_10d"] = float(df.head(10)[main_col].sum())
            if row_count >= 20:
                result["main_net_20d"] = float(df.head(20)[main_col].sum())
            return result
        except Exception as e:
            logger.debug("[akshare] capital_flow failed for %s: %s", symbol, e)
            return {}
