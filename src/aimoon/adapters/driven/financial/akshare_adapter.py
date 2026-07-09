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
        # 季报/历史年报的磁盘缓存: 历史年报变化极慢(7天),季报24小时。
        # 默认模式下 fetch/quarterly/history 三者并行,各自独立磁盘缓存避免重复网络拉取。
        self._quarterly_cache = DiskTtlCache(
            namespace="akshare_quarterly", ttl_seconds=86400
        )
        self._history_cache = DiskTtlCache(
            namespace="akshare_history", ttl_seconds=604800
        )
        # 进程内(单次运行)原始三表记忆化: fetch/quarterly/history 共享同一份
        # 东方财富原始 DataFrame,避免对利润表/资产负债表/现金流表各拉 2~3 次。
        self._raw_statements: dict[str, asyncio.Future] = {}
        # 请求间隔:在多次 API 调用之间插入延迟,降低被 WAF 拦截概率。
        # akshare 原生不设间隔选项,我们在采集器层自己控制。
        self._request_interval = 0.5  # 秒
        self._init_proxy_patch()

    def _init_proxy_patch(self) -> None:
        """若用户在 .env 配置了代理(auth_ip + auth_token),启用 akshare-proxy-patch。

        代理补丁会在 akshare 的 HTTP 请求中注入代理认证头,绕过部分 WAF 限制。
        未配置代理时不执行任何操作,走直连。
        """
        try:
            from aimoon.adapters.driven.config.settings import get_settings

            settings = get_settings()
            auth_ip = getattr(settings, "akshare_proxy_auth_ip", "")
            auth_token = getattr(settings, "akshare_proxy_auth_token", "")
            if auth_ip:
                import akshare_proxy_patch

                akshare_proxy_patch.install_patch(
                    auth_ip=auth_ip,
                    auth_token=auth_token,
                    retry=30,
                    timeout=5,
                    fast=True,
                )
                logger.info("[akshare] 代理补丁已启用: auth_ip=%s", auth_ip)
            else:
                logger.debug("[akshare] 未配置代理,走直连")
        except ImportError:
            logger.debug("[akshare] akshare-proxy-patch 未安装,跳过")
        except Exception as e:
            logger.warning("[akshare] 代理补丁初始化失败: %s", e)

    async def _throttle(self) -> None:
        """请求间隔控制:调用前等待,降低被 WAF 拦截概率。"""
        await asyncio.sleep(self._request_interval)
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

        prefix = "SH" if symbol.startswith("6") else "SZ" if symbol.startswith("0") else "BJ"
        ak_symbol = f"{prefix}{symbol}"
        try:
            income_df, bs_df, cf_df = await self._get_raw_statements(ak_symbol)
        except Exception as e:
            logger.warning("[akshare] fetch failed for %s: %s", symbol, e)
            return FinancialData(symbol=symbol, source=f"akshare_failed: {e}")

        result = FinancialData(symbol=symbol, source="akshare(东方财富)")
        if income_df is not None and not income_df.empty:
            self._parse_income_statement(result, _filter_report_type(income_df, report_type))
        if bs_df is not None and not bs_df.empty:
            self._parse_balance_sheet(result, _filter_report_type(bs_df, report_type))
        if cf_df is not None and not cf_df.empty:
            self._parse_cash_flow(result, _filter_report_type(cf_df, report_type))

        if result.revenue == 0 and result.net_profit == 0 and result.total_assets == 0:
            result.source = "akshare_empty"
        if result.net_profit != 0 and result.equity > 0:
            result.roe = round(result.net_profit / result.equity * 100, 2)

        if result.source.startswith("akshare_empty"):
            self._cache.set(cache_key, {"_empty": True})
        else:
            self._cache.set(cache_key, result.model_dump())

        return result

    async def _get_raw_statements(
        self, ak_symbol: str
    ) -> tuple[Any, Any, Any]:
        """拉取利润表/资产负债表/现金流表三张原始全量(含所有报告类型)DataFrame 一次。

        结果按 (ak_symbol) 记忆化为 asyncio.Future: 即使 fetch/quarterly/history
        在同一事件循环内并行调用,也只会真正发起一次三表网络请求,其余 await 同一 Future。
        """
        if ak_symbol in self._raw_statements:
            return await self._raw_statements[ak_symbol]
        task = asyncio.ensure_future(self._fetch_raw_statements(ak_symbol))
        self._raw_statements[ak_symbol] = task
        try:
            return await task
        except Exception:
            # 拉取失败不缓存,允许后续重试
            self._raw_statements.pop(ak_symbol, None)
            raise

    async def _fetch_raw_statements(
        self, ak_symbol: str
    ) -> tuple[Any, Any, Any]:
        """真正发起三表并行请求(在线程池),返回全量 DataFrame。"""
        await self._throttle()
        loop = asyncio.get_running_loop()
        income_df: pd.DataFrame | BaseException
        bs_df: pd.DataFrame | BaseException
        cf_df: pd.DataFrame | BaseException
        income_df, bs_df, cf_df = await asyncio.gather(
            loop.run_in_executor(None, self._sync_profit_full, ak_symbol),
            loop.run_in_executor(None, self._sync_balance_full, ak_symbol),
            loop.run_in_executor(None, self._sync_cashflow_full, ak_symbol),
            return_exceptions=True,
        )
        return (
            income_df if isinstance(income_df, pd.DataFrame) else None,
            bs_df if isinstance(bs_df, pd.DataFrame) else None,
            cf_df if isinstance(cf_df, pd.DataFrame) else None,
        )

    def _sync_profit_full(self, ak_symbol: str) -> pd.DataFrame | None:
        """同步拉取利润表全量(在线程池中运行),空则返回 None。"""
        import akshare as ak

        df = ak.stock_profit_sheet_by_report_em(symbol=ak_symbol)
        return df if (df is not None and not df.empty) else None

    def _sync_balance_full(self, ak_symbol: str) -> pd.DataFrame | None:
        """同步拉取资产负债表全量(在线程池中运行),空则返回 None。"""
        import akshare as ak

        df = ak.stock_balance_sheet_by_report_em(symbol=ak_symbol)
        return df if (df is not None and not df.empty) else None

    def _sync_cashflow_full(self, ak_symbol: str) -> pd.DataFrame | None:
        """同步拉取现金流表全量(在线程池中运行),空则返回 None。"""
        import akshare as ak

        df = ak.stock_cash_flow_sheet_by_report_em(symbol=ak_symbol)
        return df if (df is not None and not df.empty) else None

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

        # ====== 应收账款(多候选列名 + 部分匹配兜底) ======
        # 东财 API 列名不稳定,优先精确匹配,找不到则做子串匹配。
        ar = _safe_float(_get_col(df, "TOTAL_NOTE_ACCOUNTS_RECEIVABLE"))
        if ar == 0.0:
            ar = _safe_float(_get_col(df, "NOTE_ACCOUNTS_RECEIVABLE"))
        if ar == 0.0:
            ar = _safe_float(_get_col(df, "BILL_RECEIVABLE"))
        if ar == 0.0:
            ar = _safe_float(_get_col(df, "ACCOUNT_RECEIVABLE"))
        if ar == 0.0:
            # 部分匹配兜底:列名含 RECEIV 且非 PAYABLE
            for c in df.columns:
                cu = c.upper()
                if "RECEIV" in cu and "PAY" not in cu:
                    ar = _safe_float(_get_col(df, c))
                    if ar != 0.0:
                        logger.debug(
                            "[akshare] balance_sheet AR fallback matched %s", c
                        )
                        break
        if ar != 0.0:
            result.accounts_receivable = ar

        # ====== 存货 ======
        inv = _safe_float(_get_col(df, "INVENTORY"))
        if inv == 0.0:
            for c in df.columns:
                if "INVENT" in c.upper():
                    inv = _safe_float(_get_col(df, c))
                    if inv != 0.0:
                        logger.debug(
                            "[akshare] balance_sheet INV fallback matched %s", c
                        )
                        break
        if inv != 0.0:
            result.inventory = inv

        # 自诊断:若新字段全 0,把实际列名写进日志,方便用户上报。
        if ar == 0.0 and inv == 0.0:
            logger.warning(
                "[akshare] balance_sheet 应收/存货列未匹配,实际列=%s",
                list(df.columns),
            )

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

        # ====== 分配股利/利润/偿付利息现金流出(多候选+部分匹配) ======
        div = _safe_float(_get_col(df, "DIVIDEND_INTEREST_PAID"))
        if div == 0.0:
            div = _safe_float(_get_col(df, "DIVIDEND_PAID"))
        if div == 0.0:
            div = _safe_float(_get_col(df, "DIVIDEND_PROFIT_PAID"))
        if div == 0.0:
            for c in df.columns:
                cu = c.upper()
                if "DIVID" in cu or ("PROFIT" in cu and "PAY" in cu):
                    div = _safe_float(_get_col(df, c))
                    if div != 0.0:
                        logger.debug(
                            "[akshare] cashflow DIV fallback matched %s", c
                        )
                        break
        if div != 0.0:
            result.dividend_paid = div

        # 自诊断
        if div == 0.0:
            logger.info(
                "[akshare] cashflow 股利列未匹配,实际列=%s", list(df.columns)
            )

    async def fetch_financial(self, symbol: str, **kwargs: Any) -> FinancialData:
        """Alias for fetch() — matches PysnowballAdapter interface."""
        return await self.fetch(symbol, **kwargs)

    async def fetch_quarterly(self, symbol: str) -> QuarterlyFinancialData:
        """Fetch the latest non-annual report data.

        Returns the most recent 一季报/中报/三季报, preferring the latest date.
        Reuses the memoized raw profit sheet (shares the fetch with ``fetch``/
        ``fetch_history``) and adds a 24h disk cache.
        """
        prefix = "SH" if symbol.startswith("6") else "SZ" if symbol.startswith("0") else "BJ"
        ak_symbol = f"{prefix}{symbol}"

        cache_key = f"quarterly:{symbol}"
        cached = self._quarterly_cache.get(cache_key)
        if cached is not None:
            return QuarterlyFinancialData.model_validate(cached)

        try:
            income_df, _, _ = await self._get_raw_statements(ak_symbol)
        except Exception as e:
            logger.debug("[akshare] fetch_quarterly raw fetch failed: %s", e)
            return QuarterlyFinancialData(symbol=symbol, source="akshare_quarterly_failed")

        if income_df is None or income_df.empty:
            return QuarterlyFinancialData(symbol=symbol, source="akshare_quarterly_empty")

        # Filter out annual reports, sort by date descending, take the latest
        if "REPORT_TYPE" in income_df.columns:
            non_annual = income_df[income_df["REPORT_TYPE"] != "年报"]
        else:
            non_annual = income_df
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
        result = self._parse_quarterly(non_annual, symbol, report_type)
        self._quarterly_cache.set(cache_key, result.model_dump())
        return result

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

        复用 _get_raw_statements 的进程内记忆化(与 fetch/quarterly 共享三表),
        并加 7 天磁盘缓存: 重复运行不再重拉东方财富。

        任何异常均兜底返回 [],保证 pipeline v2 不因历史采集失败中断。
        """
        prefix = "SH" if symbol.startswith("6") else "SZ" if symbol.startswith("0") else "BJ"
        ak_symbol = f"{prefix}{symbol}"
        cache_key = f"history:{symbol}:{years}"
        cached = self._history_cache.get(cache_key)
        if cached is not None:
            return [FinancialData.model_validate(d) for d in cached]

        try:
            p_df, b_df, c_df = await self._get_raw_statements(ak_symbol)
        except Exception as e:
            logger.debug("[akshare] fetch_history failed for %s: %s", symbol, e)
            return []
        result = self._merge_statements(symbol, years, p_df, b_df, c_df)
        self._history_cache.set(cache_key, [fd.model_dump() for fd in result])
        return result

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
                inv = c_match.iloc[0].get("NETCASH_INVEST")
                if pd.notna(inv):
                    fd.investing_cf = float(inv)
                fin = c_match.iloc[0].get("NETCASH_FINANCE")
                if pd.notna(fin):
                    fd.financing_cf = float(fin)
            out.append(fd)
        return out
