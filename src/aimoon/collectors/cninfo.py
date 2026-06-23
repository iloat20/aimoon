"""Cninfo (巨潮资讯) company announcement collector.

Fetches official company announcements from cninfo.com.cn,
the designated information disclosure platform for Chinese listed companies.
"""

from __future__ import annotations

import re
import time
from datetime import datetime

import httpx

from ..models.social import CollectResult, SocialPost
from .base import BaseCollector

_CNINFO_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Accept": "application/json",
    "Referer": "https://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
    "Origin": "https://www.cninfo.com.cn",
}

# Category mapping for filtering announcement types
_CATEGORY_MAP = {
    "年报": "category_ndbg_szsh;",
    "半年报": "category_bndbwj_szsh;",
    "季报": "category_dljc_szsh;",
    "分红": "category_fhzcj_szsh;",
    "业绩预告": "category_yjyg_szsh;",
}


class CninfoCollector(BaseCollector):
    """Collects company announcements from 巨潮资讯 (cninfo.com.cn).

    Uses the official query API to fetch announcements by stock code.
    Returns up to 20 announcements.
    """

    name = "巨潮资讯"

    async def collect(self, symbol: str, stock_name: str = "") -> CollectResult:
        t0 = time.monotonic()
        try:
            posts = await self._fetch_announcements(symbol, stock_name)
            elapsed = (time.monotonic() - t0) * 1000
            if posts:
                return self._ok(posts, elapsed)
            return self._fail("未获取到公告", elapsed)
        except Exception as e:
            return self._fail(str(e), (time.monotonic() - t0) * 1000)

    async def _fetch_announcements(self, symbol: str, stock_name: str = "") -> list[SocialPost]:
        """Fetch company announcements via CNINFO query API."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Use stock name for better search results
            search_key = stock_name if stock_name else symbol
            payload = {
                "stock": "",
                "pageNum": "1",
                "pageSize": "20",
                "tabKey": "fulltext",
                "category": "",
                "seDate": "",
                "searchkey": search_key,
                "isHLtitle": "true",
                "sortName": "announcementTime",
                "sortType": "desc",
            }

            resp = await client.post(_CNINFO_URL, data=payload, headers=_HEADERS)
            if resp.status_code != 200:
                return []

            data = resp.json()
            items = data.get("announcements", [])
            if not items:
                return []

            # Filter to ensure only announcements for this stock
            items = [item for item in items if item.get("secCode", "") == symbol]

            if not items:
                return []

            posts: list[SocialPost] = []
            for item in items[:20]:
                try:
                    # Extract plain text title (strip HTML)
                    title_raw = item.get("announcementTitle", "")
                    title = re.sub(r"<[^>]+>", "", title_raw).strip()

                    if not title:
                        continue

                    # Build announcement URL
                    adjunct_url = item.get("adjunctUrl", "")
                    announce_id = item.get("announcementId", "")
                    org_id = item.get("orgId", "")

                    if adjunct_url:
                        url = f"https://static.cninfo.com.cn/{adjunct_url}"
                    elif announce_id:
                        url = (
                            f"https://www.cninfo.com.cn/new/disclosure/detail?"
                            f"stockCode={symbol}&announcementId={announce_id}"
                            f"&orgId={org_id}"
                        )
                    else:
                        url = ""

                    # Parse publish time (timestamp in ms)
                    pub_ts = item.get("announcementTime", 0)
                    if isinstance(pub_ts, (int, float)) and pub_ts > 1000000000000:
                        pub_ts /= 1000
                    pub_date = (
                        datetime.fromtimestamp(pub_ts).strftime("%Y-%m-%d")
                        if pub_ts
                        else ""
                    )

                    posts.append(
                        SocialPost(
                            platform="巨潮资讯",
                            title=title[:100],
                            content=title,
                            url=url,
                            author=item.get("secName", ""),
                            published_at=pub_date,
                            likes=0,
                            comments=0,
                            shares=0,
                        )
                    )
                except Exception:
                    continue

            return posts
