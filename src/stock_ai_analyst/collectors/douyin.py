"""Douyin (抖音) collector using Playwright (async).

抖音 has strong anti-scraping (WASM-based signature, device fingerprint).
This collector uses Playwright to render pages and extract data.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from ..models.social import CollectResult, SocialPost
from .base import BaseCollector

_STATE_FILE = Path.home() / ".stock-ai-analyst" / "douyin_state.json"


class DouyinCollector(BaseCollector):
    """Playwright-based 抖音 collector.

    Uses Playwright to search Douyin for stock-related videos.
    Requires login for full functionality (saves session state).
    """

    name = "抖音"

    def __init__(self) -> None:
        pass

    @staticmethod
    def login() -> None:
        """Open browser for manual login, save session state."""
        import asyncio
        asyncio.run(DouyinCollector._login_async())

    @staticmethod
    async def _login_async() -> None:
        from playwright.async_api import async_playwright

        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 720},
                locale="zh-CN",
            )
            page = await context.new_page()
            await page.goto("https://www.douyin.com")

            print("\n抖音登录页面已打开，请扫码登录")
            print("登录完成后，按回车键继续...")
            input()

            await context.storage_state(path=str(_STATE_FILE))
            print(f"✅ 登录态已保存到 {_STATE_FILE}")
            await browser.close()

    async def collect(self, symbol: str, stock_name: str = "") -> CollectResult:
        t0 = time.monotonic()

        query = stock_name or f"{symbol} 股票"
        try:
            posts = await self._search(query)
            elapsed = (time.monotonic() - t0) * 1000
            if posts:
                return self._ok(posts, elapsed)
            return self._fail("搜索无结果或无登录态", elapsed)
        except Exception as e:
            return self._fail(f"采集失败: {e}", (time.monotonic() - t0) * 1000)

    async def _search(self, query: str) -> list[SocialPost]:
        """Search 抖音 via Playwright Async API."""
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)

            opts = {}
            if _STATE_FILE.exists():
                opts["storage_state"] = str(_STATE_FILE)

            context = await browser.new_context(
                **opts,
                viewport={"width": 1280, "height": 720},
                locale="zh-CN",
            )
            page = await context.new_page()

            search_url = f"https://www.douyin.com/search/{query}?type=general"
            try:
                await page.goto(search_url, timeout=15000)
                await page.wait_for_timeout(3000)

                for _ in range(3):
                    await page.evaluate("window.scrollBy(0, 800)")
                    await page.wait_for_timeout(1000)

                posts: list[SocialPost] = []
                items = await page.query_selector_all(
                    "[class*='search-result'], "
                    "[class*='video-card'], "
                    "a[href*='/video/']"
                )

                for item in items[:10]:
                    try:
                        title_el = await item.query_selector(
                            ".title, [class*='title'], .desc, [class*='desc']"
                        )
                        title = (await title_el.inner_text()).strip() if title_el else ""

                        link = await item.get_attribute("href") or ""
                        if link and not link.startswith("http"):
                            link = f"https://www.douyin.com{link}"

                        if title:
                            posts.append(SocialPost(
                                platform="抖音",
                                title=title[:80],
                                content=title,
                                url=link,
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

            except Exception:
                await browser.close()
                return []
