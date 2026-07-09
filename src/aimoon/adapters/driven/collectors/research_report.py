"""Institutional research report collector via akshare (东方财富)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from aimoon.core.domain.entities.research import ResearchReport, ResearchReportData

from .base import DataCollector

logger = logging.getLogger(__name__)


class ResearchReportCollector(DataCollector[ResearchReportData]):
    """Fetch institutional research reports for a single A-share."""

    name = "research_report"

    def __init__(self) -> None:
        self._year_cols: dict[str, tuple[str, str]] = {}

    async def fetch(self, symbol: str, **kwargs: Any) -> ResearchReportData:
        try:
            df = await asyncio.to_thread(self._fetch_df, symbol)
            if df is None or df.empty:
                return ResearchReportData(symbol=symbol, source="all_failed")
            return self._parse(symbol, df)
        except Exception as e:
            logger.warning("[research_report_fetch] %s: %s", type(e).__name__, e)
            return ResearchReportData(symbol=symbol, source="all_failed")

    def _fetch_df(self, symbol: str):
        import akshare as ak

        return ak.stock_research_report_em(symbol=symbol)

    def _parse(self, symbol: str, df: Any) -> ResearchReportData:
        reports: list[ResearchReport] = []
        buy = hold = neutral = 0
        eps_sum = pe_sum = 0.0
        eps_count = 0
        pe_count = 0

        current_year = datetime.now().year
        one_year_ago = datetime.now() - timedelta(days=365)

        for _, row in df.iterrows():
            date_str = str(row.get("日期", ""))[:10]
            try:
                report_date = datetime.strptime(date_str, "%Y-%m-%d")
                if report_date < one_year_ago:
                    continue
            except (ValueError, TypeError):
                continue

            report = ResearchReport(
                title=str(row.get("报告名称", "")),
                institution=str(row.get("机构", "")),
                rating=str(row.get("东财评级", "")),
                industry=str(row.get("行业", "")),
                date=date_str,
                pdf_url=str(row.get("报告PDF链接", "")),
            )

            for offset, eps_attr, pe_attr in [
                (0, "eps_this_yr", "pe_this_yr"),
                (1, "eps_next_yr", "pe_next_yr"),
                (2, "eps_future_yr", "pe_future_yr"),
            ]:
                year = current_year + offset
                year_str = str(year)
                # 宽松匹配:列名需含年份,且含「收益/EPS/每股收益」或「市盈率/PE」。
                # 避免 akshare 列名微调(如「2026-盈利预测-收益」→「2026-每股收益」)导致全部漏读。
                eps_col = self._find_forecast_col(row, year_str, ("收益", "eps", "每股收益"))
                pe_col = self._find_forecast_col(row, year_str, ("市盈率", "pe"))
                if eps_col is None or pe_col is None:
                    logger.warning(
                        "研报盈利预测列缺失 year=%s 列=%s",
                        year_str, list(row.keys()),
                    )
                self._set_float(report, eps_attr, row.get(eps_col) if eps_col else None)
                self._set_float(report, pe_attr, row.get(pe_col) if pe_col else None)

            rating = report.rating
            if "买入" in rating or "推荐" in rating:
                buy += 1
            elif "增持" in rating:
                hold += 1
            elif "中性" in rating or "持有" in rating:
                neutral += 1

            if report.eps_this_yr > 0:
                eps_sum += report.eps_this_yr
                eps_count += 1
            if report.pe_this_yr > 0:
                pe_sum += report.pe_this_yr
                pe_count += 1

            reports.append(report)

        avg_eps = round(eps_sum / eps_count, 2) if eps_count else 0.0
        avg_pe = round(pe_sum / pe_count, 2) if pe_count else 0.0

        return ResearchReportData(
            symbol=symbol,
            reports=reports,
            source="akshare(东方财富研报)",
            total_count=len(reports),
            buy_count=buy,
            hold_count=hold,
            neutral_count=neutral,
            avg_eps_this_yr=avg_eps,
            avg_pe_this_yr=avg_pe,
        )

    @staticmethod
    def _set_float(report: ResearchReport, attr: str, val: object) -> None:
        try:
            v = float(str(val)) if val is not None else 0.0
        except (ValueError, TypeError):
            v = 0.0
        setattr(report, attr, v)

    @staticmethod
    def _find_forecast_col(row: dict, year_str: str, keywords: tuple[str, ...]) -> str | None:
        """在 row 列名中模糊匹配某年的盈利预测列(收益/市盈率)。"""
        for col in row.keys():
            col_lower = str(col).lower()
            if year_str in col_lower and any(k in col_lower for k in keywords):
                return str(col)
        return None
