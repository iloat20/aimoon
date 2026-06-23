"""East Money Guba (东方财富股吧) — Playwright-based collector.

Uses Playwright (not Selenium) for faster, more reliable scraping.
No ChromeDriver dependency needed.
"""

from __future__ import annotations

import time
from datetime import datetime

from ..models.social import CollectResult, SocialPost
from .base import BaseCollector


class GubaCollector(BaseCollector):
    """Collects stock posts from 东方财富股吧 using Playwright.

    Renders the guba page with Playwright's headless Chromium and
    extracts post data from the DOM.
    """

    name = "东方财富股吧"

    async def collect(self, symbol: str, stock_name: str = "") -> CollectResult:
        t0 = time.monotonic()
        market = "1" if symbol.startswith("6") else "0"
        url = f"https://guba.eastmoney.com/list,{symbol},f_{market}.html"

        try:
            posts = await self._fetch_posts(url, symbol)
            elapsed = (time.monotonic() - t0) * 1000
            if posts:
                return self._ok(posts, elapsed)
            return self._fail("页面解析为空", elapsed)
        except Exception as e:
            return self._fail(f"Playwright失败: {e}", (time.monotonic() - t0) * 1000)

    async def _fetch_posts(self, url: str, symbol: str) -> list[SocialPost]:
        """Fetch and parse guba posts using Playwright (async API)."""
        from playwright.async_api import async_playwright

        posts: list[SocialPost] = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(locale="zh-CN")
            await page.goto(url, timeout=15000)
            await page.wait_for_timeout(3000)

            rows = await page.query_selector_all("tr.listitem")
            for row in rows[:15]:
                try:
                    # Title
                    title_el = await row.query_selector("a[title], a[data-cntitle]")
                    if not title_el:
                        continue
                    title = (
                        await title_el.get_attribute("title")
                        or await title_el.get_attribute("data-cntitle")
                        or await title_el.inner_text()
                    )
                    title = title.strip()
                    href = await title_el.get_attribute("href") or ""
                    if href and not href.startswith("http"):
                        href = f"https://guba.eastmoney.com{href}"

                    # Author
                    author = ""
                    author_el = await row.query_selector(".author a")
                    if author_el:
                        author = (await author_el.inner_text()).strip()

                    # Read count
                    reads = 0
                    read_el = await row.query_selector(".read")
                    if read_el:
                        txt = (await read_el.inner_text()).strip()
                        reads = self._parse_count(txt)

                    # Comment count
                    comments = 0
                    reply_el = await row.query_selector(".reply")
                    if reply_el:
                        txt = (await reply_el.inner_text()).strip()
                        comments = int(txt.replace(",", "") or "0")

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
                except Exception:
                    continue

            await browser.close()
        posts.sort(key=lambda x: x.likes or 0, reverse=True)
        return posts

    @staticmethod
    def _parse_count(txt: str) -> int:
        """Parse count strings like '1.2万' to int."""
        txt = txt.strip()
        if not txt:
            return 0
        if "万" in txt:
            return int(float(txt.replace("万", "")) * 10000)
        if "亿" in txt:
            return int(float(txt.replace("亿", "")) * 100000000)
        try:
            return int(float(txt.replace(",", "")))
        except ValueError:
            return 0
