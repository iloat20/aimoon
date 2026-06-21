"""Data freshness checker for quality assurance.

Marks data with timeliness indicators and detects stale/suspicious data.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..models.social import SocialPost
from ..models.stock import StockQuote


class FreshnessChecker:
    """Check data freshness and flag stale items."""

    # Market trading hours (CN A-share, simplified)
    TRADING_START = 9  # 9:30am
    TRADING_END = 15  # 3:00pm

    @staticmethod
    def is_market_open() -> bool:
        """Check if A-share market is currently open."""
        now = datetime.now()
        # Weekend check
        if now.weekday() >= 5:
            return False
        # Time check (simplified)
        h = now.hour + now.minute / 60
        return 9.5 <= h <= 15.0

    @staticmethod
    def quote_freshness(q: StockQuote) -> tuple[str, str]:
        """Evaluate quote freshness.

        Returns (level, description) where level is:
        - 'fresh': < 5 min old
        - 'stale_today': same day but > 5 min
        - 'stale_yesterday': from previous trading day
        - 'stale': older data
        - 'unknown': can't determine
        """
        if not q.updated_at:
            return ("unknown", "无时间标记")

        try:
            updated = datetime.strptime(q.updated_at, "%Y-%m-%d %H:%M:%S")
            now = datetime.now()
            diff = now - updated

            if diff < timedelta(minutes=5):
                return ("fresh", "实时数据")
            elif diff < timedelta(hours=4):
                return ("stale_today", f"今日数据（{int(diff.total_seconds()/60)}分钟前）")
            elif diff < timedelta(hours=24):
                return ("stale_today", "今日收盘数据")
            elif diff < timedelta(days=2):
                return ("stale_yesterday", "昨日数据")
            else:
                return ("stale", f"{diff.days}天前数据")

        except (ValueError, TypeError):
            return ("unknown", "时间格式异常")

    @staticmethod
    def social_freshness(post: SocialPost) -> str:
        """Evaluate social post freshness.

        Returns: 'hours', 'today', 'days', 'weeks', or 'unknown'
        """
        ts = post.published_at
        if not ts:
            return "unknown"

        try:
            pub = datetime.fromisoformat(ts)
            diff = datetime.now() - pub

            if diff < timedelta(hours=4):
                return "hours"
            elif diff < timedelta(hours=24):
                return "today"
            elif diff < timedelta(days=7):
                return "days"
            else:
                return "weeks"
        except (ValueError, TypeError):
            return "unknown"

    @staticmethod
    def overall_freshness_label(quote_freshness: str, social_freshness: str) -> str:
        """Generate overall data freshness label for report display."""
        mapping = {
            "fresh": "🟢 数据实时",
            "stale_today": "🟡 今日数据",
            "stale_yesterday": "🟠 昨日数据",
            "stale": "🔴 数据延迟",
            "unknown": "⚪ 时效未知",
        }
        return mapping.get(quote_freshness, "⚪ 时效未知")
