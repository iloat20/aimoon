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
                viewport={"width": 1280, "height": 720},
                locale="zh-CN",
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
                platform=self.name,
                status="skipped",
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

            url = (
                "https://www.xiaohongshu.com/search_result"
                f"?keyword={query}&source=web_search_result_notes"
            )
            await page.goto(url)
            await page.wait_for_timeout(4000)

            # Scroll aggressively to trigger lazy loading
            last_height = 0
            no_new_count = 0
            for _ in range(25):
                await page.evaluate("window.scrollBy(0, 1200)")
                await page.wait_for_timeout(1000)
                current_height = await page.evaluate("document.body.scrollHeight")
                if current_height == last_height:
                    no_new_count += 1
                    if no_new_count >= 3:
                        break
                else:
                    no_new_count = 0
                    last_height = current_height

            # Try clicking "load more" button if exists
            try:
                load_more = await page.query_selector(
                    "button:has-text('加载更多'), "
                    "[class*='load-more'], "
                    "[class*='more-btn'], "
                    "text=查看更多"
                )
                if load_more:
                    await load_more.click()
                    await page.wait_for_timeout(2000)
            except Exception:
                pass

            posts: list[SocialPost] = []
            seen_urls: set[str] = set()

            # Try to extract from note card containers (to get likes)
            container_selectors = [
                "section.note-item",
                ".note-item",
                "[class*='note-card']",
                "[class*='feed-item']",
            ]

            for container_sel in container_selectors:
                if posts:
                    break
                containers = await page.query_selector_all(container_sel)
                if not containers:
                    continue
                for container in containers[:30]:
                    try:
                        # Find link inside container
                        link_el = await container.query_selector(
                            "a[href*='/explore/'], a[href*='/search_result/']"
                        )
                        if not link_el:
                            continue

                        link = await link_el.get_attribute("href") or ""
                        if link and not link.startswith("http"):
                            link = f"https://www.xiaohongshu.com{link}"

                        if "/search_result" in link and "/explore/" not in link:
                            continue
                        if link in seen_urls:
                            continue
                        seen_urls.add(link)

                        # Get title
                        title = ""
                        title_el = await container.query_selector(
                            ".title, .note-title, h3, [class*='title']"
                        )
                        if title_el:
                            title = (await title_el.inner_text()).strip()
                        if not title:
                            title = (await link_el.inner_text()).strip()

                        if not title or len(title) < 4:
                            continue

                        # Get like count
                        likes = 0
                        like_selectors = [
                            ".like-wrapper .count",
                            "[class*='like'] [class*='count']",
                            ".engage-bar .count",
                            "span.count",
                            "[class*='like'] span",
                            "[class*='heart'] + span",
                        ]
                        for like_sel in like_selectors:
                            like_el = await container.query_selector(like_sel)
                            if like_el:
                                like_text = (await like_el.inner_text()).strip()
                                likes = self._parse_likes(like_text)
                                break

                        posts.append(
                            SocialPost(
                                platform="小红书",
                                title=title[:80],
                                content=title,
                                url=link,
                                author="",
                                published_at="",
                                likes=likes,
                            )
                        )
                    except Exception:
                        continue
                if len(posts) >= 8:
                    break

            # Fallback: try direct link selectors if containers didn't work
            if not posts:
                link_selectors = [
                    "a[href*='/explore/']",
                    "a[href*='/search_result/']",
                ]
                for sel in link_selectors:
                    if posts:
                        break
                    items = await page.query_selector_all(sel)
                    for item in items[:30]:
                        try:
                            link = await item.get_attribute("href") or ""
                            if link and not link.startswith("http"):
                                link = f"https://www.xiaohongshu.com{link}"
                            if "/search_result" in link and "/explore/" not in link:
                                continue
                            if link in seen_urls:
                                continue
                            seen_urls.add(link)

                            title = (await item.inner_text()).strip()
                            if not title or len(title) < 4:
                                title_el = await item.query_selector(
                                    ".title, .note-title, h3"
                                )
                                if title_el:
                                    title = (await title_el.inner_text()).strip()
                            if title and len(title) >= 4:
                                posts.append(
                                    SocialPost(
                                        platform="小红书",
                                        title=title[:80],
                                        content=title,
                                        url=link,
                                        author="",
                                        published_at="",
                                        likes=0,
                                    )
                                )
                        except Exception:
                            continue
                    if len(posts) >= 5:
                        break

            await browser.close()
            # Sort by likes descending
            posts.sort(key=lambda x: x.likes or 0, reverse=True)
            return posts[:25]

    @staticmethod
    def _parse_likes(text: str) -> int:
        """Parse like count text like '123', '1.2万', '12.5w'."""
        if not text:
            return 0
        text = text.strip().lower()
        try:
            if "万" in text or "w" in text:
                num = text.replace("万", "").replace("w", "").strip()
                return int(float(num) * 10000)
            return int(text)
        except (ValueError, TypeError):
            return 0
