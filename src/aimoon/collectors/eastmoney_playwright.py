"""East Money Guba (东方财富股吧) — unified collector.

Strategies (tried in order, errors don't abort):
1. Playwright DOM rendering — most reliable for post content
2. akshare sentiment API — enriches sentiment field
3. akshare HTML parsing — lightweight fallback
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any

import httpx

from ..models.social import CollectResult, SocialPost
from ..utils import parse_chinese_count, silent_failure
from .base import BaseCollector

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://guba.eastmoney.com/",
}


class GubaCollector(BaseCollector):
    """Collects stock posts from 东方财富股吧.

    Internal strategy chain: Playwright → akshare HTML.
    """

    name = "东方财富股吧"

    def __init__(self) -> None:
        self._browser: Any = None

    def set_browser(self, browser: Any) -> None:
        """Set shared browser instance for Playwright reuse."""
        self._browser = browser

    async def collect(self, symbol: str, stock_name: str = "") -> CollectResult:
        t0 = time.monotonic()
        posts: list[SocialPost] = []
        source = ""

        # Strategy 1: Playwright DOM rendering
        with silent_failure("guba_playwright_fetch"):
            pw_posts = await self._fetch_playwright(symbol)
            if pw_posts:
                posts.extend(pw_posts)
                source = "Playwright"

        # Strategy 2: akshare HTML parsing (lightweight fallback)
        if not posts:
            with silent_failure("guba_html_fetch"):
                html_posts = await self._fetch_guba_html(symbol)
                if html_posts:
                    posts.extend(html_posts)
                    source = "akshare(HTML)"

        elapsed = (time.monotonic() - t0) * 1000
        if posts:
            result = self._ok(posts, elapsed)
            result.error = source
            return result
        return self._fail("无法获取股吧数据", elapsed)

    # ---- Playwright ----

    async def _fetch_playwright(self, symbol: str) -> list[SocialPost]:
        """Render guba page with Playwright and extract DOM data.

        Uses the "全部" (all) tab — f_{market} — which shows only this
        stock's posts, then sorts by read count to get the hottest ones.
        """
        from playwright.async_api import async_playwright

        market = "1" if symbol.startswith("6") else "0"
        url = f"https://guba.eastmoney.com/list,{symbol},f_{market}.html"

        posts: list[SocialPost] = []

        owns_browser = self._browser is None
        p = None
        browser = self._browser
        try:
            if owns_browser:
                p = await async_playwright().start()
                browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(locale="zh-CN")
            try:
                page = await context.new_page()
                await page.goto(url, timeout=15000)
                await page.wait_for_timeout(3000)

                rows = await page.query_selector_all("tr.listitem")
                for row in rows[:30]:
                    try:
                        title_el = await row.query_selector("a[data-postid]")
                        if not title_el:
                            continue
                        title = (await title_el.inner_text()).strip()
                        href = await title_el.get_attribute("href") or ""
                        if href and not href.startswith("http"):
                            href = f"https://guba.eastmoney.com{href}"

                        author = ""
                        author_el = await row.query_selector(".author a")
                        if author_el:
                            author = (await author_el.inner_text()).strip()

                        reads = 0
                        read_el = await row.query_selector(".read")
                        if read_el:
                            reads = parse_chinese_count(
                                (await read_el.inner_text()).strip()
                            )

                        comments = 0
                        reply_el = await row.query_selector(".reply")
                        if reply_el:
                            comments = int(
                                (await reply_el.inner_text()).strip().replace(",", "")
                                or "0"
                            )

                        if title and len(title) >= 4:
                            posts.append(
                                SocialPost(
                                    platform="东方财富股吧",
                                    title=title[:100],
                                    content=title,
                                    url=href,
                                    author=author,
                                    published_at=datetime.now().isoformat(),
                                    likes=reads,
                                    comments=comments,
                                    views=reads,
                                )
                            )
                    except Exception as e:
                        logging.warning(
                            "[guba_playwright_post_parse] %s: %s",
                            type(e).__name__,
                            e,
                        )
                        continue
            finally:
                await context.close()
        finally:
            if owns_browser:
                if browser:
                    await browser.close()
                if p:
                    await p.stop()

        return posts[:20]

    # ---- akshare HTML fallback ----

    async def _fetch_guba_html(self, symbol: str) -> list[SocialPost]:
        """Parse guba HTML page for this stock's latest post titles.

        Uses the "全部" (all) tab — f_{market} — which shows only this
        stock's posts, sorted by time (latest first).
        """
        async with httpx.AsyncClient(headers=_HEADERS, timeout=15.0) as client:
            market = "1" if symbol.startswith("6") else "0"
            url = f"https://guba.eastmoney.com/list,{symbol},f_{market}.html"
            resp = await client.get(url)
            if resp.status_code != 200:
                return []

            html = resp.text
            posts: list[SocialPost] = []

            title_pattern = re.compile(
                r'<a[^>]*href="(/news[^"]*)"[^>]*>([^<]*)</a>', re.IGNORECASE
            )
            matches = title_pattern.findall(html)

            for href, title in matches[:20]:
                title = title.strip()
                if not title or len(title) < 5:
                    continue

                posts.append(
                    SocialPost(
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
                    )
                )

            return posts
