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
from typing import Any

import httpx

from aimoon.adapters.driven.common.parsers import parse_chinese_count
from aimoon.adapters.driven.common.retry import silent_failure
from aimoon.core.domain.entities.social import SocialPost
from aimoon.core.domain.value_objects.collect_result import CollectResult

from .base import BaseCollector

_HEADERS = {
    "Referer": "https://guba.eastmoney.com/",
}


class GubaCollector(BaseCollector):
    """Collects stock posts from 东方财富股吧.

    Internal strategy chain: Playwright → akshare HTML.
    """

    name = "东方财富股吧"

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._browser: Any = None
        self._http: httpx.AsyncClient | None = http_client

    def set_browser(self, browser: Any) -> None:
        """Set shared browser instance for Playwright reuse."""
        self._browser = browser

    def _playwright_enabled(self) -> bool:
        """是否启用股吧 Playwright 渲染。默认关闭(见 settings.guba_playwright_enabled),
        以节省一次完整浏览器启动 + 渲染的算力开销。"""
        try:
            from aimoon.adapters.driven.config.settings import get_settings

            return get_settings().guba_playwright_enabled
        except Exception:
            return False

    async def collect(self, symbol: str, stock_name: str = "") -> CollectResult:
        t0 = time.monotonic()
        posts: list[SocialPost] = []
        source = ""

        # Strategy 1 (default): lightweight akshare HTML parsing — no browser, fast.
        with silent_failure("guba_html_fetch"):
            html_posts = await self._fetch_guba_html(symbol)
            if html_posts:
                posts.extend(html_posts)
                source = "akshare(HTML)"

        # Strategy 2: Playwright DOM rendering — only if HTML yielded nothing,
        # and only when explicitly enabled (default off, to avoid launching a browser).
        if not posts and self._playwright_enabled():
            with silent_failure("guba_playwright_fetch"):
                pw_posts = await self._fetch_playwright(symbol)
                if pw_posts:
                    posts.extend(pw_posts)
                    source = "Playwright"

        elapsed = (time.monotonic() - t0) * 1000
        if posts:
            return CollectResult(
                platform=self.name,
                status="success",
                posts=posts,
                count=len(posts),
                elapsed_ms=elapsed,
                error=source,
            )
        return self._fail("无法获取股吧数据", elapsed)

    # ---- Playwright ----

    async def _fetch_playwright(self, symbol: str) -> list[SocialPost]:
        """Render guba page with Playwright and extract DOM data.

        Uses the "全部" (all) tab — f_{market} — which shows only this
        stock's posts, then sorts by read count to get the hottest ones.
        """
        from aimoon.adapters.driven.common.browser import browser_session

        # 市场码: 沪市(6)→"1", 深市(0/3)→"0"; 北交所(8/4/9)无对应 Guba 子列表,
        # 落到 "0" 后该标签页拉不到数据(已知限制, 安全降级为 mock)。
        market = "1" if symbol.startswith("6") else "0" if symbol.startswith("0") else "0"
        url = f"https://guba.eastmoney.com/list,{symbol},f_{market}.html"

        posts: list[SocialPost] = []

        async with browser_session(self._browser) as (_browser, context):
            page = await context.new_page()
            await page.goto(url, timeout=15000)
            try:
                # 事件驱动等待: 列表行出现即继续, 避免盲目硬等 3s。
                await page.wait_for_selector("tr.listitem", timeout=8000)
            except Exception:
                pass

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
                        reads = parse_chinese_count((await read_el.inner_text()).strip())

                    comments = 0
                    reply_el = await row.query_selector(".reply")
                    if reply_el:
                        comments = int(
                            (await reply_el.inner_text()).strip().replace(",", "") or "0"
                        )

                    if title and len(title) >= 4:
                        posts.append(
                            SocialPost(
                                platform="东方财富股吧",
                                title=title[:100],
                                content=title,
                                url=href,
                                author=author,
                                published_at="",
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

        return posts[:20]

    # ---- akshare HTML fallback ----

    async def _fetch_guba_html(self, symbol: str) -> list[SocialPost]:
        """Parse guba HTML page for this stock's latest post titles.

        Uses the "全部" (all) tab — f_{market} — which shows only this
        stock's posts, sorted by time (latest first).
        """
        from aimoon.adapters.driven.config.settings import get_settings

        headers = {
            **_HEADERS,
            "User-Agent": get_settings().default_user_agent,
        }
        async with (self._http or httpx.AsyncClient(headers=headers, timeout=15.0)) as client:
            # 市场码: 沪市(6)→"1", 深市(0/3)→"0"; 北交所(8/4/9)无对应 Guba 子列表,
            # 落到 "0" 后该标签页拉不到数据(已知限制, 安全降级为 mock)。
            market = "1" if symbol.startswith("6") else "0" if symbol.startswith("0") else "0"
            url = f"https://guba.eastmoney.com/list,{symbol},f_{market}.html"
            resp = await client.get(url)
            if resp.status_code != 200:
                return []

            html = resp.text
            posts: list[SocialPost] = []

            # 允许 <a> 内嵌套标签(标题常被 <span>/<em> 包裹),
            # 内层文本在下方统一 strip 标签。
            title_pattern = re.compile(
                r'<a[^>]*href="(/news[^"]*)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL
            )
            matches = title_pattern.findall(html)

            seen: set[str] = set()
            for href, title in matches[:40]:
                title = re.sub(r"<[^>]+>", "", title).strip()
                if not title or len(title) < 5 or href in seen:
                    continue
                seen.add(href)

                posts.append(
                    SocialPost(
                        platform="东方财富股吧",
                        title=title[:80],
                        content=title,
                        url=f"https://guba.eastmoney.com{href}",
                        author="",
                        published_at="",
                        likes=0,
                        comments=0,
                        shares=0,
                        views=0,
                    )
                )

            return posts[:20]
