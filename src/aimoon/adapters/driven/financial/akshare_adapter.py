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
            result = await self._fetch_all(symbol, report_type)
        except Exception as e:
            logger.warning("[akshare] fetch failed for %s: %s", symbol, e)
            return FinancialData(symbol=symbol, source=f"akshare_failed: {e}")

        if result.source.startswith("akshare_empty"):
            self._cache.set(cache_key, {"_empty": True})
        else:
            self._cache.set(cache_key, result.model_dump())

        return result

    async def _fetch_all(self, symbol: str, report_type: str = "年报") -> FinancialData:
        """Fetch and merge data from all three financial statements in parallel."""
        result = FinancialData(symbol=symbol, source="akshare(东方财富)")
        prefix = "SH" if symbol.startswith("6") else "SZ" if symbol.startswith("0") else "BJ"
        ak_symbol = f"{prefix}{symbol}"

        loop = asyncio.get_running_loop()
        income_df: pd.DataFrame | BaseException
        bs_df: pd.DataFrame | BaseException
        cf_df: pd.DataFrame | BaseException
        income_df, bs_df, cf_df = await asyncio.gather(
            loop.run_in_executor(None, self._sync_income, ak_symbol, report_type),
            loop.run_in_executor(None, self._sync_balance, ak_symbol, report_type),
            loop.run_in_executor(None, self._sync_cashflow, ak_symbol, report_type),
            return_exceptions=True,
        )

        if isinstance(income_df, pd.DataFrame) and not income_df.empty:
            self._parse_income_statement(result, income_df)
        if isinstance(bs_df, pd.DataFrame) and not bs_df.empty:
            self._parse_balance_sheet(result, bs_df)
        if isinstance(cf_df, pd.DataFrame) and not cf_df.empty:
            self._parse_cash_flow(result, cf_df)

        if result.revenue == 0 and result.net_profit == 0 and result.total_assets == 0:
            result.source = "akshare_empty"
        if result.net_profit != 0 and result.equity > 0:
            result.roe = round(result.net_profit / result.equity * 100, 2)

        return result

    def _sync_income(self, ak_symbol: str, report_type: str):
        """同步获取利润表（在线程池中运行）。"""
        import akshare as ak

        df = ak.stock_profit_sheet_by_report_em(symbol=ak_symbol)
        if df is not None and not df.empty:
            return _filter_report_type(df, report_type)
        return None

    def _sync_balance(self, ak_symbol: str, report_type: str):
        """同步获取资产负债表。"""
        import akshare as ak

        df = ak.stock_balance_sheet_by_report_em(symbol=ak_symbol)
        if df is not None and not df.empty:
            return _filter_report_type(df, report_type)
        return None

    def _sync_cashflow(self, ak_symbol: str, report_type: str):
        """同步获取现金流表。"""
        import akshare as ak

        df = ak.stock_cash_flow_sheet_by_report_em(symbol=ak_symbol)
        if df is not None and not df.empty:
            return _filter_report_type(df, report_type)
        return None

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

        if result.total_assets > 0:
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
        return await self.fetch(symbol, **kwargs)

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
        if "REPORT_DATE" in non_annual.columns:
            non_annual = non_annual.sort_values("REPORT_DATE", ascending=False)
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
        market = "sh" if symbol.startswith("6") else "sz" if symbol.startswith(("0", "3")) else "bj"
        try:
            df = await asyncio.to_thread(ak.stock_individual_fund_flow, stock=symbol, market=market)
            if df is None or df.empty:
                return {}
            # Data is sorted asc by date (oldest first); tail() gets recent days
            row_count = len(df)
            main_col = "主力净流入-净额"
            result: dict[str, Any] = {"recent_date": str(df.iloc[-1]["日期"])}
            if row_count >= 5:
                result["main_net_5d"] = float(df.tail(5)[main_col].sum())
            if row_count >= 3:
                result["main_net_3d"] = float(df.tail(3)[main_col].sum())
            if row_count >= 10:
                result["main_net_10d"] = float(df.tail(10)[main_col].sum())
            if row_count >= 20:
                result["main_net_20d"] = float(df.tail(20)[main_col].sum())
            return result
        except Exception as e:
            logger.debug("[akshare] capital_flow failed for %s: %s", symbol, e)
            return {}

    async def fetch_history(self, symbol: str, years: int = 3) -> list[FinancialData]:
        """拉取近 N 年年报,按报告期降序返回。

        并行拉取 profit_sheet / balance_sheet / cash_flow_sheet 三张表,
        按 (symbol, 年报日期) 内连接对齐,一次性补全收入、净利润、ROE、权益、OCF。

        任何异常均兜底返回 [],保证 pipeline v2 不因历史采集失败中断。
        """
        prefix = "SH" if symbol.startswith("6") else "SZ" if symbol.startswith("0") else "BJ"
        ak_symbol = f"{prefix}{symbol}"
        loop = asyncio.get_running_loop()
        p_task = loop.run_in_executor(None, ak.stock_profit_sheet_by_report_em, ak_symbol)
        b_task = loop.run_in_executor(None, ak.stock_balance_sheet_by_report_em, ak_symbol)
        c_task = loop.run_in_executor(None, ak.stock_cash_flow_sheet_by_report_em, ak_symbol)
        try:
            p_df, b_df, c_df = await asyncio.gather(p_task, b_task, c_task)
        except Exception as e:
            logger.debug("[akshare] fetch_history failed for %s: %s", symbol, e)
            return []
        return self._merge_statements(symbol, years, p_df, b_df, c_df)

    @staticmethod
    def _annual(df: pd.DataFrame | None) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        if "REPORT_TYPE" in df.columns:
            df = df[df["REPORT_TYPE"] == "年报"]
        if "REPORT_DATE" in df.columns:
            df = df.sort_values("REPORT_DATE", ascending=False).drop_duplicates(
                subset=["REPORT_DATE"], keep="first"
            )
        return df

    @staticmethod
    def _yr(date_val) -> str:
        if date_val is None or pd.isna(date_val):
            return ""
        return str(date_val)[:10]

    @staticmethod
    def _first_positive(row: pd.Series, cols: list[str]) -> tuple[float | None, str | None]:
        """按优先级找第一个 >0 的字段值,返回 (value, column_name)。
        找不到返回 (None, None)。
        """
        for c in cols:
            if c not in row.index:
                continue
            v = row.get(c)
            try:
                if pd.notna(v) and float(v) > 0:
                    return float(v), c
            except (TypeError, ValueError):
                continue
        return None, None

    def _merge_statements(
        self, symbol: str, years: int,
        p_df: pd.DataFrame | None, b_df: pd.DataFrame | None, c_df: pd.DataFrame | None,
    ) -> list[FinancialData]:
        p_df = self._annual(p_df)
        b_df = self._annual(b_df)
        c_df = self._annual(c_df)
        out: list[FinancialData] = []
        for _, pr in p_df.head(years).iterrows():
            y = self._yr(pr.get("REPORT_DATE"))
            fd = FinancialData(symbol=symbol, report_period=y, source="akshare(东方财富)")

            # 收入字段按行业 fallback:
            #   通用行业: TOTAL_OPERATE_INCOME
            #   金融行业(保险/银行/证券): OPERATE_INCOME → INSURANCE_INCOME → BANK_INTEREST_INCOME
            rev, rev_src = self._first_positive(
                pr,
                [
                    "TOTAL_OPERATE_INCOME",
                    "OPERATE_INCOME",
                    "INSURANCE_INCOME",
                    "EARNED_PREMIUM",
                    "BANK_INTEREST_INCOME",
                    "FEE_AND_COMMISSION_INCOME",
                ],
            )
            if rev is not None:
                fd.revenue = float(rev)
            rey = pr.get(f"{rev_src}_YOY") if rev_src else None
            if rey is None:
                rey = pr.get("TOTAL_OPERATE_INCOME_YOY")
            if pd.notna(rey) and float(rey) != 0:
                fd.revenue_yoy = float(rey)
            np_ = pr.get("NETPROFIT")
            if pd.notna(np_) and float(np_) != 0:
                fd.net_profit = float(np_)
            npy = pr.get("NETPROFIT_YOY")
            if pd.notna(npy) and float(npy) != 0:
                fd.net_profit_yoy = float(npy)
            eps = pr.get("BASIC_EPS")
            if pd.notna(eps) and float(eps) > 0:
                fd.eps = float(eps)

            b_match = b_df[b_df["REPORT_DATE"].astype(str).str[:10] == y]
            if not b_match.empty:
                ta = b_match.iloc[0].get("TOTAL_ASSETS")
                tl = b_match.iloc[0].get("TOTAL_LIABILITIES")
                if pd.notna(ta):
                    fd.total_assets = float(ta)
                if pd.notna(tl):
                    fd.total_liabilities = float(tl)
                if fd.total_assets > 0:
                    fd.equity = fd.total_assets - fd.total_liabilities
                if fd.net_profit != 0 and fd.equity > 0:
                    fd.roe = round(fd.net_profit / fd.equity * 100, 2)

            c_match = c_df[c_df["REPORT_DATE"].astype(str).str[:10] == y]
            if not c_match.empty:
                ocf = c_match.iloc[0].get("NETCASH_OPERATE")
                if pd.notna(ocf):
                    fd.operating_cf = float(ocf)
            out.append(fd)
        return out
