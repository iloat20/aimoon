"""Toutiao (今日头条) collector using Playwright (async).

Searches so.toutiao.com for stock-related articles.
"""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

from ..models.social import CollectResult, SocialPost
from .base import BaseCollector


class ToutiaoCollector(BaseCollector):
    """Collects stock-related articles from Toutiao (今日头条)."""

    name = "今日头条"

    async def collect(self, symbol: str, stock_name: str = "") -> CollectResult:
        t0 = time.monotonic()
        keyword = stock_name or symbol
        try:
            posts = await self._search(keyword)
            elapsed = (time.monotonic() - t0) * 1000
            if posts:
                return self._ok(posts, elapsed)
            return self._fail("未找到相关文章", elapsed)
        except Exception as e:
            return self._fail(str(e), (time.monotonic() - t0) * 1000)

    async def _search(self, keyword: str) -> list[SocialPost]:
        """Search Toutiao via Playwright Async API."""
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(locale="zh-CN")

            url = f"https://so.toutiao.com/search?keyword={keyword}+股票&pd=information"
            await page.goto(url, timeout=20000)
            await page.wait_for_timeout(3000)

            # Scroll aggressively to trigger lazy loading
            for _ in range(10):
                await page.evaluate("window.scrollBy(0, 1000)")
                await page.wait_for_timeout(800)

            posts: list[SocialPost] = []
            # Use broad selector for Toutiao search result links
            title_elements = await page.query_selector_all(
                "a[href*='/jump']"
            )

            for el in title_elements[:10]:
                try:
                    title = await el.inner_text()
                    title = title.strip()
                    if not title or len(title) < 5:
                        continue

                    href = await el.get_attribute("href") or ""

                    actual_url = ""
                    if "jtoken" in href and "url=" in href:
                        import re
                        m = re.search(r'url=([^&]+)', href)
                        if m:
                            from urllib.parse import unquote
                            actual_url = unquote(m.group(1))

                    posts.append(SocialPost(
                        platform="今日头条",
                        title=title[:80],
                        content=title,
                        url=actual_url or href,
                        author="",
                        published_at="",
                        likes=0,
                        comments=0,
                        shares=0,
                    ))
                except Exception:
                    continue

            await browser.close()
            return posts
