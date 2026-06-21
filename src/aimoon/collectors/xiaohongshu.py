"""Xiaohongshu (小红书) collector using Playwright (async).

Requires login cookie. Use `login()` once to save session state,
then subsequent calls reuse the saved state.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..models.social import CollectResult, SocialPost
from .base import BaseCollector

_STATE_FILE = Path.home() / ".stock-ai-analyst" / "xhs_state.json"


class XiaohongshuCollector(BaseCollector):
    """Playwright-based 小红书 collector.
    Uses saved login state file for authenticated access.
    """

    name = "小红书"

    @staticmethod
    def login() -> None:
        """Open browser for manual login, save session state."""
        import asyncio
        asyncio.run(XiaohongshuCollector._login_async())

    @staticmethod
    async def _login_async() -> None:
        from playwright.async_api import async_playwright

        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 720}, locale="zh-CN",
            )
            page = await context.new_page()
            await page.goto("https://www.xiaohongshu.com")

            print("\n小红书登录页面已打开，请手动登录（扫码/手机号）")
            print("登录完成后，按回车键继续...")
            input()

            await context.storage_state(path=str(_STATE_FILE))
            print(f"✅ 登录态已保存到 {_STATE_FILE}")
            await browser.close()

    async def collect(self, symbol: str, stock_name: str = "") -> CollectResult:
        t0 = time.monotonic()

        if not _STATE_FILE.exists():
            elapsed = (time.monotonic() - t0) * 1000
            return CollectResult(
                platform=self.name, status="skipped",
                error="未登录。运行 XiaohongshuCollector.login() 手动登录",
                elapsed_ms=elapsed,
            )

        query = stock_name or f"{symbol} 股票"
        try:
            posts = await self._search(query)
            elapsed = (time.monotonic() - t0) * 1000
            if posts:
                return self._ok(posts, elapsed)
            return self._fail("搜索无结果", elapsed)
        except Exception as e:
            return self._fail(f"采集失败: {e}", (time.monotonic() - t0) * 1000)

    async def _search(self, query: str) -> list[SocialPost]:
        """Search 小红书 via Playwright Async API with saved login state."""
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                storage_state=str(_STATE_FILE),
                viewport={"width": 1280, "height": 720},
                locale="zh-CN",
            )
            page = await context.new_page()

            url = f"https://www.xiaohongshu.com/search_result?keyword={query}&source=web_search_result_notes"
            await page.goto(url)
            await page.wait_for_timeout(3000)

            # Scroll to trigger lazy loading
            for _ in range(6):
                await page.evaluate("window.scrollBy(0, 1200)")
                await page.wait_for_timeout(1500)

            posts: list[SocialPost] = []

            # Try multiple selector patterns for note cards
            for selector in [
                "section.note-item",
                "a[href*='/explore/']",
                ".note-item",
                "[class*='note'] a[href*='explore']",
            ]:
                items = await page.query_selector_all(selector)
                if items:
                    for item in items[:10]:
                        try:
                            title_el = await item.query_selector(".title, .note-title, h3, [class*='title']")
                            title = (await title_el.inner_text()).strip() if title_el else ""

                            link = await item.get_attribute("href") or ""
                            if link and not link.startswith("http"):
                                link = f"https://www.xiaohongshu.com{link}"

                            if title and len(title) >= 4:
                                posts.append(SocialPost(
                                    platform="小红书", title=title[:80],
                                    content=title, url=link,
                                    author="", published_at="",
                                ))
                        except Exception:
                            continue
                    if posts:
                        break  # Found results with this selector

            await browser.close()
            return posts
