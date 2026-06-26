"""Shared HTTP utilities — common headers, client factory."""

from __future__ import annotations

import httpx

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {"User-Agent": DEFAULT_USER_AGENT}

CNINFO_HEADERS = {
    **DEFAULT_HEADERS,
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Accept": "application/json",
    "Referer": "https://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
    "Origin": "https://www.cninfo.com.cn",
}


def create_client(
    timeout: float = 15.0,
    headers: dict[str, str] | None = None,
    follow_redirects: bool = True,
) -> httpx.AsyncClient:
    """Create a shared httpx AsyncClient with sensible defaults."""
    return httpx.AsyncClient(
        headers=headers or DEFAULT_HEADERS,
        timeout=timeout,
        follow_redirects=follow_redirects,
    )
