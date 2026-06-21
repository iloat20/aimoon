"""WeChat Official Account (微信公众号) article collector.

Searches for stock-related articles via Sogou WeChat search.
"""

from __future__ import annotations

import time
from datetime import datetime

import httpx

from ..models.social import CollectResult, SocialPost
from .base import BaseCollector


class WechatCollector(BaseCollector):
    """Collects stock-related articles from WeChat Official Accounts via Sogou search."""

    name = "微信公众号"

    async def collect(self, symbol: str, stock_name: str = "") -> CollectResult:
        t0 = time.monotonic()

        try:
            posts = await self._search(symbol, stock_name)
            elapsed = (time.monotonic() - t0) * 1000
            if posts:
                return self._ok(posts, elapsed)
            return self._fail("未找到相关文章", elapsed)
        except Exception as e:
            return self._fail(str(e), (time.monotonic() - t0) * 1000)

    async def _search(self, symbol: str, stock_name: str) -> list[SocialPost]:
        """Search Sogou WeChat for articles."""
        keyword = stock_name or symbol

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        }

        posts: list[SocialPost] = []
        async with httpx.AsyncClient(headers=headers, timeout=10.0) as client:
            try:
                url = "https://weixin.sogou.com/weixin"
                params = {
                    "type": "2",
                    "query": f"{keyword} 股票",
                    "ie": "utf8",
                }
                resp = await client.get(url, params=params)

                if resp.status_code != 200 or "请输入验证码" in resp.text:
                    return []

                # Parse search results from HTML
                import re
                # Extract article titles and URLs
                items = re.findall(
                    r'<h3[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                    resp.text, re.DOTALL
                )

                for i, (href, raw_title) in enumerate(items[:10]):
                    title = re.sub(r"<[^>]+>", "", raw_title).strip()
                    if not title:
                        continue

                    posts.append(SocialPost(
                        platform="微信公众号",
                        title=title[:80],
                        content=title,
                        url=href if href.startswith("http") else f"https:{href}",
                        author="",
                        published_at=datetime.now().isoformat(),
                    ))
            except Exception:
                pass

        return posts
