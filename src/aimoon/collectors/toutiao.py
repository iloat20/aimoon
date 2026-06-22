"""Toutiao (今日头条) collector using Playwright (async).

Searches so.toutiao.com for stock-related articles.
"""

from __future__ import annotations

import time

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
            last_height = 0
            no_new_count = 0
            for _ in range(15):
                await page.evaluate("window.scrollBy(0, 1000)")
                await page.wait_for_timeout(800)
                current_height = await page.evaluate("document.body.scrollHeight")
                if current_height == last_height:
                    no_new_count += 1
                    if no_new_count >= 3:
                        break
                else:
                    no_new_count = 0
                    last_height = current_height

            posts: list[SocialPost] = []
            seen_urls: set[str] = set()

            # Try to find search result containers first
            containers = await page.query_selector_all(
                "[class*='result-content'], [class*='cs-view'], [class*='result']"
            )

            if containers:
                for container in containers[:15]:
                    try:
                        link_el = await container.query_selector("a[href*='/jump']")
                        if not link_el:
                            continue

                        title = (await link_el.inner_text()).strip()
                        if not title or len(title) < 5:
                            continue

                        href = await link_el.get_attribute("href") or ""
                        actual_url = ""
                        if "jtoken" in href and "url=" in href:
                            import re
                            m = re.search(r"url=([^&]+)", href)
                            if m:
                                from urllib.parse import unquote
                                actual_url = unquote(m.group(1))

                        final_url = actual_url or href
                        if final_url in seen_urls:
                            continue
                        seen_urls.add(final_url)

                        # Try to extract engagement metrics
                        likes = 0
                        comments = 0
                        source = ""

                        # Look for like/engagement elements
                        for sel in [
                            "[class*='like'] [class*='count']",
                            "[class*='like'] span",
                            "span[class*='count']",
                        ]:
                            like_el = await container.query_selector(sel)
                            if like_el:
                                txt = (await like_el.inner_text()).strip()
                                likes = self._parse_count(txt)
                                if likes > 0:
                                    break

                        # Look for comment count
                        for sel in [
                            "[class*='comment'] [class*='count']",
                            "[class*='comment'] span",
                        ]:
                            comment_el = await container.query_selector(sel)
                            if comment_el:
                                txt = (await comment_el.inner_text()).strip()
                                comments = self._parse_count(txt)
                                if comments > 0:
                                    break

                        # Look for source
                        for sel in [
                            "[class*='source']",
                            "[class*='media']",
                            "span[class*='tag']",
                        ]:
                            source_el = await container.query_selector(sel)
                            if source_el:
                                source = (await source_el.inner_text()).strip()
                                if source:
                                    break

                        posts.append(
                            SocialPost(
                                platform="今日头条",
                                title=title[:80],
                                content=title,
                                url=final_url,
                                author=source,
                                published_at="",
                                likes=likes,
                                comments=comments,
                            )
                        )
                    except Exception:
                        continue
            else:
                # Fallback: direct link parsing
                title_elements = await page.query_selector_all("a[href*='/jump']")
                for el in title_elements[:15]:
                    try:
                        title = (await el.inner_text()).strip()
                        if not title or len(title) < 5:
                            continue

                        href = await el.get_attribute("href") or ""
                        actual_url = ""
                        if "jtoken" in href and "url=" in href:
                            import re
                            m = re.search(r"url=([^&]+)", href)
                            if m:
                                from urllib.parse import unquote
                                actual_url = unquote(m.group(1))

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
                    except Exception:
                        continue

            await browser.close()
            posts.sort(key=lambda x: x.likes or 0, reverse=True)
            return posts[:15]

    @staticmethod
    def _parse_count(txt: str) -> int:
        """Parse count strings like '1.2万' to int."""
        if not txt:
            return 0
        txt = txt.strip().lower()
        try:
            if "万" in txt or "w" in txt:
                num = txt.replace("万", "").replace("w", "").strip()
                return int(float(num) * 10000)
            if "亿" in txt or "b" in txt:
                num = txt.replace("亿", "").replace("b", "").strip()
                return int(float(num) * 100000000)
            return int(txt.replace(",", ""))
        except (ValueError, TypeError):
            return 0
