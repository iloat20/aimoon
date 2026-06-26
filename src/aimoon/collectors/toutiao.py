"""Toutiao (今日头条) collector using Playwright (async).

Searches so.toutiao.com for stock-related articles.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..models.social import CollectResult, SocialPost
from ..utils import extract_toutiao_url, parse_chinese_count
from .base import BaseCollector


class ToutiaoCollector(BaseCollector):
    """Collects stock-related articles from Toutiao (今日头条)."""

    name = "今日头条"

    def __init__(self) -> None:
        self._browser: Any = None

    def set_browser(self, browser: Any) -> None:
        """Set shared browser instance for Playwright reuse."""
        self._browser = browser

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
        """Search Toutiao via Playwright Async API.

        Scrapes two pages (offset 0 + offset 10) to collect ~20 results.
        """
        from playwright.async_api import async_playwright

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

                posts: list[SocialPost] = []
                seen_urls: set[str] = set()

                for offset in (0, 10):
                    url = (
                        f"https://so.toutiao.com/search"
                        f"?keyword={keyword}+股票&pd=information&offset={offset}"
                    )
                    await page.goto(url, timeout=20000)
                    await page.wait_for_timeout(3000)

                    # Scroll to trigger lazy loading
                    last_height = 0
                    no_new_count = 0
                    for _ in range(10):
                        await page.evaluate("""
                            const container = document.querySelector('#results')
                            if (container) {
                                container.scrollTop = container.scrollHeight
                            }
                            window.scrollBy(0, 2000)
                        """)
                        await page.wait_for_timeout(800)
                        js = (
                            "document.querySelector('#results')"
                            "?.scrollHeight || document.body.scrollHeight"
                        )
                        current_height = await page.evaluate(js)
                        if current_height == last_height:
                            no_new_count += 1
                            if no_new_count >= 3:
                                break
                        else:
                            no_new_count = 0
                            last_height = current_height

                    page_posts = await self._parse_page(page, seen_urls)
                    posts.extend(page_posts)

                return posts[:20]
            finally:
                await context.close()
        finally:
            if owns_browser:
                if browser:
                    await browser.close()
                if p:
                    await p.stop()

    async def _parse_page(
        self, page: Any, seen_urls: set[str]
    ) -> list[SocialPost]:
        """Parse all result cards on the current page."""
        posts: list[SocialPost] = []

        cards = await page.query_selector_all("#results .result-content")
        for card in cards[:15]:
            try:
                link_el = await card.query_selector("a[href*='/jump']")
                if not link_el:
                    continue

                title = (await link_el.inner_text()).strip()
                if not title or len(title) < 5:
                    continue

                href = await link_el.get_attribute("href") or ""
                actual_url = extract_toutiao_url(href)
                final_url = actual_url or href
                if final_url in seen_urls:
                    continue
                seen_urls.add(final_url)

                likes = 0
                comments = 0
                source = ""

                for sel in [
                    "[class*='like'] [class*='count']",
                    "[class*='like'] span",
                    "span[class*='count']",
                ]:
                    like_el = await card.query_selector(sel)
                    if like_el:
                        txt = (await like_el.inner_text()).strip()
                        likes = parse_chinese_count(txt)
                        if likes > 0:
                            break

                for sel in [
                    "[class*='comment'] [class*='count']",
                    "[class*='comment'] span",
                ]:
                    comment_el = await card.query_selector(sel)
                    if comment_el:
                        txt = (await comment_el.inner_text()).strip()
                        comments = parse_chinese_count(txt)
                        if comments > 0:
                            break

                for sel in [
                    "[class*='source']",
                    "[class*='media']",
                    "span[class*='tag']",
                ]:
                    source_el = await card.query_selector(sel)
                    if source_el:
                        source = (await source_el.inner_text()).strip()
                        if source:
                            break

                published_at = ""
                for sel in [
                    "[class*='time']",
                    "span[class*='date']",
                    "time",
                ]:
                    time_el = await card.query_selector(sel)
                    if time_el:
                        published_at = (await time_el.inner_text()).strip()
                        if published_at:
                            break

                posts.append(
                    SocialPost(
                        platform="今日头条",
                        title=title[:80],
                        content=title,
                        url=final_url,
                        author=source,
                        published_at=published_at,
                        likes=likes,
                        comments=comments,
                    )
                )
            except Exception as e:
                logging.warning(
                    "[toutiao_card_parse] %s: %s",
                    type(e).__name__,
                    e,
                )
                continue

        # Fallback: direct link parsing
        if not posts:
            title_elements = await page.query_selector_all(
                "#results a[href*='/jump']"
            )
            for el in title_elements[:15]:
                try:
                    title = (await el.inner_text()).strip()
                    if not title or len(title) < 5:
                        continue

                    href = await el.get_attribute("href") or ""
                    actual_url = extract_toutiao_url(href)
                    final_url = actual_url or href
                    if final_url in seen_urls:
                        continue
                    seen_urls.add(final_url)

                    posts.append(
                        SocialPost(
                            platform="今日头条",
                            title=title[:80],
                            content=title,
                            url=final_url,
                            author="",
                            published_at="",
                            likes=0,
                            comments=0,
                        )
                    )
                except Exception as e:
                    logging.warning(
                        "[toutiao_link_parse] %s: %s",
                        type(e).__name__,
                        e,
                    )
                    continue

        return posts
