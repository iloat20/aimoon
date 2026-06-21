"""Data format validator for quality assurance."""

from __future__ import annotations

from datetime import datetime, timedelta

from ..models.stock import FinancialData, StockQuote
from ..models.social import SocialPost


class FormatValidator:
    """Validate collected data formats and flag anomalies."""

    @staticmethod
    def validate_quote(q: StockQuote) -> list[str]:
        """Validate quote data, return list of warnings."""
        warnings: list[str] = []

        if q.price <= 0:
            warnings.append("股价为零或负值，数据异常")
        if q.price > 5000:
            warnings.append(f"股价异常高: {q.price}")

        if q.change_pct > 20 or q.change_pct < -20:
            warnings.append(f"涨跌幅异常: {q.change_pct}%")

        if q.volume < 0:
            warnings.append("成交量为负值")

        if q.high < q.low:
            warnings.append(f"最高价({q.high})低于最低价({q.low})")

        if q.turnover < 0 or q.turnover > 200:
            warnings.append(f"换手率异常: {q.turnover}%")

        if q.pe and (q.pe < 0 or q.pe > 10000):
            warnings.append(f"PE异常: {q.pe}")

        return warnings

    @staticmethod
    def validate_financial(f: FinancialData) -> list[str]:
        """Validate financial data, return list of warnings."""
        warnings: list[str] = []

        if not f.report_period:
            warnings.append("缺少报告期信息")

        if f.revenue <= 0:
            warnings.append("营收为零或负值")

        if f.total_assets <= 0:
            warnings.append("总资产为零或负值")

        if f.total_assets > 0 and f.total_liabilities > f.total_assets:
            warnings.append("负债大于总资产（资不抵债）")

        if f.roe and abs(f.roe) > 500:
            warnings.append(f"ROE异常: {f.roe}%")

        return warnings

    @staticmethod
    def validate_social_post(p: SocialPost) -> list[str]:
        """Validate social post, return list of warnings."""
        warnings: list[str] = []

        if not p.title and not p.content:
            warnings.append("帖子标题和内容均为空")

        if p.likes < 0 or p.comments < 0 or p.shares < 0:
            warnings.append("互动数据含负值")

        if p.published_at:
            try:
                dt = datetime.fromisoformat(p.published_at)
                if dt > datetime.now() + timedelta(days=1):
                    warnings.append("发布时间在未来")
            except ValueError:
                warnings.append("发布时间格式异常")

        return warnings

    @staticmethod
    def is_stale(iso_timestamp: str, max_age_hours: int = 24) -> bool:
        """Check if data is stale (older than max_age_hours)."""
        if not iso_timestamp:
            return True
        try:
            dt = datetime.fromisoformat(iso_timestamp)
            age = datetime.now() - dt
            return age > timedelta(hours=max_age_hours)
        except ValueError:
            return True
