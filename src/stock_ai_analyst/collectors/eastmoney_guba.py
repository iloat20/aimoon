"""East Money Guba (东方财富股吧) social data collector.

Uses akshare sentiment API + direct HTTP scraping for robust data collection.
"""

from __future__ import annotations

import re
import time
from datetime import datetime

import httpx

from ..models.social import CollectResult, SocialPost
from .base import BaseCollector

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://guba.eastmoney.com/",
}


class EastMoneyGubaCollector(BaseCollector):
    """Collects stock-related posts from East Money Guba."""

    name = "东方财富股吧"

    async def collect(self, symbol: str, stock_name: str = "") -> CollectResult:
        t0 = time.monotonic()

        posts: list[SocialPost] = []

        # Strategy 1: akshare sentiment data
        try:
            sentiment_posts = await self._fetch_akshare_sentiment(symbol)
            posts.extend(sentiment_posts)
        except Exception:
            pass

        # Strategy 2: Direct guba HTML parsing
        try:
            guba_posts = await self._fetch_guba_html(symbol)
            posts.extend(guba_posts)
        except Exception:
            pass

        elapsed = (time.monotonic() - t0) * 1000

        if posts:
            return self._ok(posts, elapsed)
        return self._fail("无法获取股吧数据", elapsed)

    async def _fetch_akshare_sentiment(self, symbol: str) -> list[SocialPost]:
        """Get sentiment data via akshare."""
        try:
            import akshare as ak

            df = ak.stock_comment_detail_scrd_desire_em(symbol=symbol)
            if df is None or df.empty:
                return []

            posts: list[SocialPost] = []
            latest = df.head(5)
            for _, row in latest.iterrows():
                desire = float(row.get("参与意愿", 0))
                sentiment = "positive" if desire > 50 else ("negative" if desire < 30 else "neutral")
                posts.append(SocialPost(
                    platform="东方财富股吧",
                    title=f"[热度数据] 参与意愿: {desire:.1f}",
                    content=f"东方财富股吧参与意愿指标: {desire:.1f} (5日均值: {row.get('5日平均参与意愿', 0)})",
                    url=f"https://guba.eastmoney.com/list,{symbol}.html",
                    author="东方财富数据",
                    published_at=str(row.get("交易日期", "")),
                    likes=int(desire * 10),
                    comments=0,
                    shares=0,
                    views=int(desire * 100),
                    sentiment=sentiment,
                ))
            return posts
        except Exception:
            return []

    async def _fetch_guba_html(self, symbol: str) -> list[SocialPost]:
        """Parse guba HTML page for post titles."""
        async with httpx.AsyncClient(headers=_HEADERS, timeout=15.0) as client:
            market = "1" if symbol.startswith("6") else "2"
            url = f"https://guba.eastmoney.com/list,{symbol},f_{market}.html"
            resp = await client.get(url)
            if resp.status_code != 200:
                return []

            html = resp.text
            posts: list[SocialPost] = []

            # Extract post titles and URLs from HTML
            # Pattern: article list items with title links
            title_pattern = re.compile(
                r'<a\s+href="(/news[^"]*)"[^>]*title="([^"]*)"',
                re.IGNORECASE
            )
            matches = title_pattern.findall(html)

            for i, (href, title) in enumerate(matches[:10]):
                title = title.strip()
                if not title or len(title) < 5:
                    continue

                posts.append(SocialPost(
                    platform="东方财富股吧",
                    title=title[:80],
                    content=title,
                    url=f"https://guba.eastmoney.com{href}",
                    author="",
                    published_at=datetime.now().isoformat(),
                    likes=0,
                    comments=0,
                    shares=0,
                    views=0,
                ))

            return posts
