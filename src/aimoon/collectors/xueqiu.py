"""Xueqiu (雪球) social data collector.

Collects hot posts, stock quotes (with PE), and discussions.
Requires cookie authentication (xq_a_token, u).
Uses stock.xueqiu.com subdomain to avoid WAF on main domain.
"""

from __future__ import annotations

import time
from datetime import datetime

import httpx

from ..config.settings import get_settings
from ..models.social import CollectResult, SocialPost
from ..models.stock import StockQuote
from ..utils import to_xueqiu_symbol
from .base import BaseCollector

# stock.xueqiu.com avoids WAF on main xueqiu.com for quote APIs
_XQ_QUOTE_URL = "https://stock.xueqiu.com/v5/stock/quote.json"
# xueqiu.com hot list (works with cookie, no WAF for this endpoint)
_XQ_HOT_URL = "https://xueqiu.com/statuses/hot/list.json"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


class XueqiuCollector(BaseCollector):
    """Collects stock information from Xueqiu."""

    name = "雪球"

    def __init__(
        self, cookie: str = "", client: httpx.AsyncClient | None = None
    ) -> None:
        settings = get_settings()
        self._cookie = cookie or settings.xueqiu_cookie
        self._client = client
        self._u_value = ""
        self._token_value = ""
        if self._cookie:
            for part in self._cookie.split(";"):
                part = part.strip()
                if part.startswith("u="):
                    self._u_value = part[2:]
                elif part.startswith("xq_a_token="):
                    self._token_value = part[len("xq_a_token=") :]

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {**_DEFAULT_HEADERS}
            if self._cookie:
                headers["Cookie"] = self._cookie
            self._client = httpx.AsyncClient(
                headers=headers, timeout=15.0, follow_redirects=True
            )
        return self._client

    def _symbol_xq(self, symbol: str) -> str:
        """Convert code to Xueqiu symbol format: SH600519."""
        return to_xueqiu_symbol(symbol)

    async def fetch_quote(self, symbol: str) -> StockQuote | None:
        """Fetch enhanced quote with PE from Xueqiu."""
        if not self._cookie:
            return None
        client = await self._get_client()
        xq_sym = self._symbol_xq(symbol)
        try:
            resp = await client.get(
                _XQ_QUOTE_URL, params={"symbol": xq_sym, "extend": "detail"}
            )
            if resp.status_code != 200 or "aliyun_waf" in resp.text:
                return None
            data = resp.json()
            q = data.get("data", {}).get("quote", {})
            if not q:
                return None

            prev_close = float(q.get("last_close", q.get("open", 0)) or 0)
            price = float(q.get("current", 0) or 0)
            change = price - prev_close
            change_pct = (change / prev_close * 100) if prev_close else 0

            return StockQuote(
                symbol=symbol,
                name=str(q.get("name", "")),
                price=round(price, 2),
                change=round(change, 2),
                change_pct=round(change_pct, 2),
                volume=int(float(q.get("volume", 0) or 0)),
                amount=float(q.get("amount", 0) or 0),
                high=float(q.get("high", 0) or 0),
                low=float(q.get("low", 0) or 0),
                open=float(q.get("open", 0) or 0),
                prev_close=round(prev_close, 2),
                turnover=float(q.get("turnover_rate", 0) or 0),
                pe=float(q.get("pe_ttm", q.get("pe_lyr", 0)) or 0),
                source="雪球",
                updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
        except Exception:
            return None

    async def collect(self, symbol: str, stock_name: str = "") -> CollectResult:
        t0 = time.monotonic()

        if not self._cookie:
            return self._fail("未配置雪球Cookie", (time.monotonic() - t0) * 1000)

        posts: list[SocialPost] = []
        seen_titles: set[str] = set()

        # Strategy 1: Stock-specific search (精确搜索，优先使用)
        try:
            search_posts = await self._search_stock_posts(symbol, stock_name)
            for p in search_posts:
                if p.title not in seen_titles:
                    seen_titles.add(p.title)
                    posts.append(p)
        except Exception:
            pass

        # Strategy 2: Hot posts — only take posts that mention the stock
        if len(posts) < 10:
            try:
                hot_posts = await self._fetch_hot_posts()
                for p in hot_posts:
                    if p.title not in seen_titles and (
                        symbol in p.title
                        or p.title.startswith(stock_name[:2])
                        or stock_name[:2] in p.title
                    ):
                        seen_titles.add(p.title)
                        posts.append(p)
            except Exception:
                pass

        # Strategy 3: Playwright fallback (bypass WAF with real browser)
        if len(posts) < 3:
            try:
                pw_posts = await self._collect_via_playwright(symbol, stock_name)
                for p in pw_posts:
                    if p.title not in seen_titles:
                        seen_titles.add(p.title)
                        posts.append(p)
            except Exception:
                pass

        posts = posts[:10]

        elapsed = (time.monotonic() - t0) * 1000
        if posts:
            return self._ok(posts, elapsed)
        return self._fail(
            "雪球社交数据被阿里云WAF封锁（滑块验证），headless浏览器无法绕过。"
            "行情/财务数据正常（stock.xueqiu.com子域名）。"
            "建议：安装AgentReach作为备选，或依赖其他舆情源（股吧/头条）。",
            elapsed,
        )

    async def _fetch_hot_posts(self) -> list[SocialPost]:
        """Fetch trending posts from Xueqiu hot list."""
        client = await self._get_client()
        try:
            resp = await client.get(_XQ_HOT_URL, params={"page": "1", "size": "20"})
            if resp.status_code != 200 or "aliyun_waf" in resp.text:
                return []
            data = resp.json()
        except Exception:
            return []

        items = data.get("items", [])
        posts: list[SocialPost] = []
        for item in items[:10]:
            try:
                os = item.get("original_status", {}) or {}
                title = os.get("title", "") or os.get("description", "")
                text = os.get("description", "") or title

                posts.append(
                    SocialPost(
                        platform="雪球",
                        title=title[:80] if title else "(无内容)",
                        content=text,
                        url=(
                            f"https://xueqiu.com/"
                            f"{os.get('user', {}).get('profile', '')}"
                            f"/{os.get('id', '')}"
                        ),
                        author=str(os.get("user", {}).get("screen_name", "")),
                        published_at=(
                            datetime.fromtimestamp(
                                os.get("created_at", 0) / 1000
                            ).isoformat()
                            if os.get("created_at")
                            else ""
                        ),
                        likes=int(os.get("like_count", 0)),
                        comments=int(os.get("reply_count", 0)),
                        shares=int(os.get("retweet_count", 0)),
                        views=int(os.get("view_count", 0)),
                    )
                )
            except Exception:
                continue
        return posts

    async def _search_stock_posts(
        self, symbol: str, stock_name: str
    ) -> list[SocialPost]:
        """Search for stock-specific posts."""
        client = await self._get_client()

        # Try v1 search API on main domain
        query = symbol if not stock_name else f"{symbol} {stock_name}"
        params = {"q": query, "count": "20", "page": "1"}
        if self._token_value:
            params["access_token"] = self._token_value
        if self._u_value:
            params["u"] = self._u_value

        try:
            resp = await client.get(
                "https://xueqiu.com/query/v1/search/status",
                params=params,
            )
            if resp.status_code != 200 or "aliyun_waf" in resp.text:
                return []
            data = resp.json()
        except Exception:
            return []

        items = data.get("list", data.get("items", []))
        posts: list[SocialPost] = []
        for item in items[:10]:
            try:
                text = item.get("text", item.get("title", ""))
                import re

                text = re.sub(r"<[^>]+>", "", text)

                posts.append(
                    SocialPost(
                        platform="雪球",
                        title=text[:80] if text else "(无内容)",
                        content=text,
                        url=f"https://xueqiu.com{item.get('target', '')}",
                        author=str(item.get("user", {}).get("screen_name", "")),
                        published_at=(
                            datetime.fromtimestamp(
                                item.get("created_at", 0) / 1000
                            ).isoformat()
                            if item.get("created_at")
                            else ""
                        ),
                        likes=int(item.get("like_count", 0)),
                        comments=int(item.get("reply_count", 0)),
                        shares=int(item.get("retweet_count", 0)),
                        views=int(item.get("view_count", 0)),
                    )
                )
            except Exception:
                continue
        return posts

    async def _collect_via_playwright(
        self, symbol: str, stock_name: str
    ) -> list[SocialPost]:
        """Fallback: use Playwright to bypass WAF via real browser JS execution."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return []

        import re

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context(locale="zh-CN")

                if self._cookie:
                    cookies = []
                    for part in self._cookie.split(";"):
                        part = part.strip()
                        if "=" in part:
                            n, v = part.split("=", 1)
                            cookies.append(
                                {
                                    "name": n.strip(),
                                    "value": v.strip(),
                                    "domain": ".xueqiu.com",
                                    "path": "/",
                                }
                            )
                    if cookies:
                        await context.add_cookies(cookies)  # type: ignore[arg-type]

                page = await context.new_page()
                await page.goto(
                    "https://xueqiu.com", wait_until="domcontentloaded", timeout=30000
                )

                for _wait_round in range(3):
                    await page.wait_for_timeout(4000)
                    has_waf = await page.evaluate(
                        "() => !!document.querySelector('iframe[id*=waf]') || "
                        "document.title.includes('WAF') || "
                        "document.title.includes('验证') || "
                        "document.querySelector('[class*=challenge]') !== null"
                    )
                    if not has_waf:
                        break

                query = symbol if not stock_name else f"{symbol} {stock_name}"
                result = None
                for _retry in range(2):
                    result = await page.evaluate(
                        """(q) => fetch(
                            'https://xueqiu.com/query/v1/search/status?q=' +
                            encodeURIComponent(q) + '&count=20&page=1',
                            {credentials: 'include'}
                        ).then(r => {
                            const t = r.text();
                            return t;
                        }).then(t => {
                            try { return JSON.parse(t); } catch(e) { return {_raw: t}; }
                        }).catch(() => null)""",
                        query,
                    )
                    if result and not result.get("_raw"):
                        break
                    await page.wait_for_timeout(3000)

                posts: list[SocialPost] = []
                if result and not result.get("_raw"):
                    items = result.get("list", result.get("items", []))
                    for item in items[:10]:
                        try:
                            text = item.get("text", item.get("title", ""))
                            text = re.sub(r"<[^>]+>", "", text)
                            posts.append(
                                SocialPost(
                                    platform="雪球",
                                    title=text[:80] if text else "(无内容)",
                                    content=text,
                                    url=f"https://xueqiu.com{item.get('target', '')}",
                                    author=str(
                                        item.get("user", {}).get("screen_name", "")
                                    ),
                                    published_at=(
                                        datetime.fromtimestamp(
                                            item.get("created_at", 0) / 1000
                                        ).isoformat()
                                        if item.get("created_at")
                                        else ""
                                    ),
                                    likes=int(item.get("like_count", 0)),
                                    comments=int(item.get("reply_count", 0)),
                                    shares=int(item.get("retweet_count", 0)),
                                    views=int(item.get("view_count", 0)),
                                )
                            )
                        except Exception:
                            continue

                if len(posts) < 3:
                    hot_result = await page.evaluate(
                        """() => fetch(
                            'https://xueqiu.com/statuses/hot/list.json?page=1&size=20',
                            {credentials: 'include'}
                        ).then(r => r.text()).then(t => {
                            try { return JSON.parse(t); } catch(e) { return {_raw: t}; }
                        }).catch(() => null)"""
                    )
                    if hot_result and not hot_result.get("_raw"):
                        for item in hot_result.get("items", [])[:10]:
                            try:
                                os = item.get("original_status", {}) or {}
                                title = os.get("title", "") or os.get("description", "")
                                text = os.get("description", "") or title
                                if symbol not in title and (
                                    not stock_name
                                    or stock_name[:2] not in title
                                ):
                                    continue
                                posts.append(
                                    SocialPost(
                                        platform="雪球",
                                        title=title[:80] if title else "(无内容)",
                                        content=text,
                                        url=(
                                            f"https://xueqiu.com/"
                                            f"{os.get('user', {}).get('profile', '')}"
                                            f"/{os.get('id', '')}"
                                        ),
                                        author=str(
                                            os.get("user", {}).get(
                                                "screen_name", ""
                                            )
                                        ),
                                        published_at=(
                                            datetime.fromtimestamp(
                                                os.get("created_at", 0) / 1000
                                            ).isoformat()
                                            if os.get("created_at")
                                            else ""
                                        ),
                                        likes=int(os.get("like_count", 0)),
                                        comments=int(os.get("reply_count", 0)),
                                        shares=int(os.get("retweet_count", 0)),
                                        views=int(os.get("view_count", 0)),
                                    )
                                )
                            except Exception:
                                continue

                return posts
            finally:
                await browser.close()
