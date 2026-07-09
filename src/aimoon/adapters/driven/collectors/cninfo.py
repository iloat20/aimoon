"""Cninfo (巨潮资讯) company announcement collector.

Fetches official company announcements from cninfo.com.cn,
the designated information disclosure platform for Chinese listed companies.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone

import httpx

from aimoon.core.domain.entities.social import SocialPost
from aimoon.core.domain.value_objects.collect_result import CollectResult

from .base import BaseCollector

logger = logging.getLogger(__name__)

_CNINFO_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Accept": "application/json",
    "Referer": "https://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
    "Origin": "https://www.cninfo.com.cn",
}


class CninfoCollector(BaseCollector):
    """Collects company announcements from 巨潮资讯 (cninfo.com.cn).

    Uses the official query API to fetch announcements by stock code.
    Returns up to 20 announcements.
    """

    name = "巨潮资讯"

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http = http_client

    async def collect(self, symbol: str, stock_name: str = "") -> CollectResult:
        t0 = time.monotonic()
        try:
            posts = await self._fetch_announcements(symbol, stock_name)
            elapsed = (time.monotonic() - t0) * 1000
            if posts:
                return self._ok(posts, elapsed)
            return self._fail("未获取到公告", elapsed)
        except (httpx.HTTPError, ValueError, TypeError) as e:
            logger.warning("[cninfo_collect] %s: %s", type(e).__name__, e)
            return self._fail(str(e), (time.monotonic() - t0) * 1000)

    async def _fetch_announcements(self, symbol: str, stock_name: str = "") -> list[SocialPost]:
        """Fetch company announcements via CNINFO query API."""
        from aimoon.adapters.driven.config.settings import get_settings

        headers = {
            **_HEADERS,
            "User-Agent": get_settings().default_user_agent,
        }
        async with (self._http or httpx.AsyncClient(timeout=15.0)) as client:
            search_key = stock_name if stock_name else symbol
            payload = {
                "stock": "",
                "pageNum": "1",
                "pageSize": "30",
                "tabKey": "fulltext",
                "category": "",
                "seDate": "",
                "searchkey": search_key,
                "isHLtitle": "true",
                "sortName": "announcementTime",
                "sortType": "desc",
            }

            resp = await client.post(_CNINFO_URL, data=payload, headers=headers)
            if resp.status_code != 200:
                return []

            data = resp.json()
            # 兼容两种返回结构: 公告数组在顶层 {"announcements":[...]}
            # 或包在 {"data":{"announcements":[...]}}。
            items = (
                data.get("announcements")
                or (data.get("data") or {}).get("announcements")
                or []
            )
            if not items:
                return []

            # Filter to ensure only announcements for this stock
            items = [item for item in items if symbol in item.get("secCode", "")]

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
                        continue

                    # Parse publish time (timestamp in ms or string)
                    pub_ts = item.get("announcementTime", 0)
                    if isinstance(pub_ts, str):
                        pub_date = pub_ts[:10]
                    else:
                        if isinstance(pub_ts, (int, float)) and pub_ts > 1000000000000:
                            pub_ts /= 1000
                        pub_date = (
                            datetime.fromtimestamp(
                                pub_ts, tz=timezone(timedelta(hours=8))
                            ).strftime("%Y-%m-%d")
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
                except Exception as e:
                    logger.debug(
                        "[cninfo_announcement_parse] %s: %s",
                        type(e).__name__,
                        e,
                    )
                    continue

            return posts
