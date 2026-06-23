"""WeChat Official Account (微信公众号) article collector.

Uses Playwright to bypass Sogou anti-spider and fetch articles.
"""

from __future__ import annotations

import time
from datetime import datetime

from ..models.social import CollectResult, SocialPost
from .base import BaseCollector


class WechatCollector(BaseCollector):
    """Collects stock-related articles from WeChat
    Official Accounts via Sogou search with Playwright.
    """

    name = "微信公众号"

    async def collect(self, symbol: str, stock_name: str = "") -> CollectResult:
        t0 = time.monotonic()
        keyword = stock_name if stock_name else symbol
        posts: list[SocialPost] = []
        seen_titles: set[str] = set()

        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    locale="zh-CN",
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                )
                page = await context.new_page()

                # Visit main page first to get cookies
                await page.goto("https://weixin.sogou.com/", timeout=15000)
                await page.wait_for_timeout(2000)

                for pg in range(1, 4):  # pages 1-3
                    try:
                        url = f"https://weixin.sogou.com/weixin?type=2&query={keyword}%20股票&page={pg}"
                        await page.goto(url, timeout=15000)
                        await page.wait_for_timeout(2000)

                        content = await page.content()
                        if "请输入验证码" in content or "antispider" in content.lower():
                            break

                        # Extract articles
                        items = await page.query_selector_all("ul.news-list li")
                        for item in items[:20]:
                            try:
                                title_el = await item.query_selector("h3 a")
                                if not title_el:
                                    continue
                                title = (await title_el.inner_text()).strip()
                                if not title or title in seen_titles:
                                    continue

                                href = await title_el.get_attribute("href") or ""
                                if href.startswith("/"):
                                    href = f"https://weixin.sogou.com{href}"

                                author_el = await item.query_selector("a.account")
                                author = ""
                                if author_el:
                                    author = (await author_el.inner_text()).strip()

                                seen_titles.add(title)
                                posts.append(
                                    SocialPost(
                                        platform="微信公众号",
                                        title=title[:80],
                                        content=title,
                                        url=href,
                                        author=author,
                                        published_at=datetime.now().isoformat(),
                                    )
                                )
                            except Exception:
                                continue

                        if len(posts) >= 20:
                            break

                    except Exception:
                        break

                await browser.close()

        except ImportError:
            pass
        except Exception:
            pass

        elapsed = (time.monotonic() - t0) * 1000
        if posts:
            return self._ok(posts[:20], elapsed)
        return self._fail("未获取到微信文章", elapsed)
