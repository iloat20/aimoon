"""WeChat Official Account (微信公众号) article collector.

Searches for stock-related articles via Sogou WeChat search.
"""

from __future__ import annotations

import time
from datetime import datetime

import httpx

from ..models.social import CollectResult, SocialPost
from .base import BaseCollector


class WechatCollector(BaseCollector):
    """Collects stock-related articles from WeChat
    Official Accounts via Sogou search.
    """

    name = "微信公众号"

    async def collect(self, symbol: str, stock_name: str = "") -> CollectResult:
        t0 = time.monotonic()

        try:
            posts = await self._search(symbol, stock_name)
            elapsed = (time.monotonic() - t0) * 1000
            if posts:
                return self._ok(posts, elapsed)
            return self._fail("未找到相关文章", elapsed)
        except Exception as e:
            return self._fail(str(e), (time.monotonic() - t0) * 1000)

    async def _search(self, symbol: str, stock_name: str) -> list[SocialPost]:
        """Search Sogou WeChat for articles."""
        keyword = stock_name or symbol

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        }

        posts: list[SocialPost] = []
        async with httpx.AsyncClient(
            headers=headers, timeout=10.0, follow_redirects=True
        ) as client:
            try:
                url = "https://weixin.sogou.com/weixin"
                params = {
                    "type": "2",
                    "query": f"{keyword} 股票",
                    "ie": "utf8",
                }
                resp = await client.get(url, params=params)

                if resp.status_code != 200 or "请输入验证码" in resp.text:
                    return []

                import re

                # Extract article items with title, URL, and account name
                # Pattern: <li> blocks containing <h3> with <a> link and account info
                pattern = re.compile(
                    r'<li[^>]*>.*?<h3[^>]*>\s*<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
                    r'<a[^>]*class="account"[^>]*>(.*?)</a>.*?</li>',
                    re.DOTALL,
                )

                for match in pattern.finditer(resp.text):
                    raw_url, raw_title, raw_author = match.groups()
                    title = re.sub(r"<[^>]+>", "", raw_title).strip()
                    author = re.sub(r"<[^>]+>", "", raw_author).strip()

                    if not title:
                        continue

                    # Sogou returns relative URLs, prefix with domain
                    article_url = raw_url
                    if article_url.startswith("/"):
                        article_url = "https://weixin.sogou.com" + article_url
                    # Clean HTML entities
                    article_url = article_url.replace("&amp;", "&")

                    posts.append(
                        SocialPost(
                            platform="微信公众号",
                            title=title[:80],
                            content=title,
                            url=article_url,
                            author=author,
                            published_at=datetime.now().isoformat(),
                        )
                    )

                # Fallback: simpler regex if the above didn't match
                if not posts:
                    simple_pattern = re.compile(
                        r'<h3[^>]*>\s*<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                        re.DOTALL,
                    )
                    for match in simple_pattern.finditer(resp.text):
                        raw_url, raw_title = match.groups()
                        title = re.sub(r"<[^>]+>", "", raw_title).strip()
                        if not title:
                            continue
                        article_url = raw_url
                        if article_url.startswith("/"):
                            article_url = "https://weixin.sogou.com" + article_url
                        article_url = article_url.replace("&amp;", "&")
                        posts.append(
                            SocialPost(
                                platform="微信公众号",
                                title=title[:80],
                                content=title,
                                url=article_url,
                                author="",
                                published_at=datetime.now().isoformat(),
                            )
                        )
            except Exception:
                pass

        return posts[:10]
