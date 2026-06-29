"""Web search tool for DeepSeek tool-calling integration.

Multi-backend: Bing (primary, works in China) → DuckDuckGo (fallback).
All free, no API key required.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time as _time_module

import httpx

_search_client: httpx.AsyncClient | None = None

_search_cache: dict[str, tuple[float, str]] = {}
_SEARCH_CACHE_TTL = 300  # 5 分钟


def _get_cached_search(query: str) -> str | None:
    """获取缓存的搜索结果，过期返回 None。"""
    key = hashlib.md5(query.encode()).hexdigest()
    if key in _search_cache:
        ts, result = _search_cache[key]
        if _time_module.time() - ts < _SEARCH_CACHE_TTL:
            return result
        del _search_cache[key]
    return None


def _set_cached_search(query: str, result: str) -> None:
    """缓存搜索结果。"""
    key = hashlib.md5(query.encode()).hexdigest()
    _search_cache[key] = (_time_module.time(), result)
    if len(_search_cache) > 100:
        _search_cache.clear()


def _get_search_client() -> httpx.AsyncClient:
    global _search_client
    if _search_client is None:
        _search_client = httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=True,
            limits=httpx.Limits(max_keepalive_connections=5),
        )
    return _search_client


_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "搜索互联网获取实时信息，用于查询股票公告、财务数据、行情、行业动态等。"
            "当需要最新数据或训练数据中未包含的信息时调用此工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": '搜索关键词，如"茅台 2025年报 营收 分产品"',
                },
            },
            "required": ["query"],
        },
    },
}


def get_tool_definitions() -> list[dict]:
    """Return the tool schema for the DeepSeek API."""
    return [_TOOL_DEFINITION]


async def execute_web_search(query: str, max_results: int = 5) -> str:
    """Execute a web search with fallback: Bing → DuckDuckGo. Results are cached."""
    # 检查缓存
    cached = _get_cached_search(query)
    if cached is not None:
        return cached

    result = await _search_bing(query, max_results)
    if not result:
        result = await _search_ddg(query, max_results)
    if not result:
        result = "搜索失败: 所有搜索引擎均不可用"

    # 缓存结果
    _set_cached_search(query, result)
    return result


async def _search_bing(query: str, max_results: int) -> str:
    """Search via cn.bing.com (works in mainland China)."""
    from aimoon.adapters.driven.config.settings import get_settings

    ua = get_settings().default_user_agent
    try:
        client = _get_search_client()
        resp = await client.get(
            "https://cn.bing.com/search",
            params={"q": query},
            headers={"User-Agent": ua},
        )
        resp.raise_for_status()
        return _parse_bing(resp.text, max_results)
    except Exception as e:
        logging.warning("[web_search_bing] %s: %s", type(e).__name__, e)
        return ""


def _parse_bing(html: str, max_results: int) -> str:
    """Parse Bing search results."""
    results: list[dict[str, str]] = []

    blocks = re.findall(
        r'<li class="b_algo".*?</li>',
        html,
        re.DOTALL,
    )

    for block in blocks[:max_results]:
        url_m = re.search(r'<a[^>]*href="([^"]+)"', block)
        title_m = re.search(r"<a[^>]*>(.*?)</a>", block, re.DOTALL)
        # Bing snippet is in <div class="b_caption"><p>
        snippet_m = re.search(r"<p[^>]*>(.*?)</p>", block, re.DOTALL)

        if not url_m or not title_m:
            continue

        url = url_m.group(1)
        title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip()
        snippet = re.sub(r"<[^>]+>", "", snippet_m.group(1)).strip() if snippet_m else ""

        if title:
            results.append({"title": title, "snippet": snippet, "url": url})

    if not results:
        return ""

    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['title']}")
        if r["snippet"]:
            lines.append(f"    {r['snippet']}")
        lines.append(f"    来源: {r['url']}")
        lines.append("")

    return "\n".join(lines)


async def _search_ddg(query: str, max_results: int) -> str:
    """Search via DuckDuckGo HTML (fallback, may be blocked in China)."""
    from aimoon.adapters.driven.config.settings import get_settings

    ua = get_settings().default_user_agent
    try:
        client = _get_search_client()
        resp = await client.post(
            "https://html.duckduckgo.com/html/",
            headers={"User-Agent": ua},
            data={"q": query, "b": ""},
        )
        resp.raise_for_status()
        return _parse_ddg(resp.text, max_results)
    except Exception as e:
        logging.warning("[web_search_ddg] %s: %s", type(e).__name__, e)
        return ""


def _parse_ddg(html: str, max_results: int) -> str:
    """Parse DuckDuckGo HTML search results."""
    results: list[dict[str, str]] = []

    blocks = re.findall(
        r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>'
        r'.*?<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        html,
        re.DOTALL,
    )

    for url, title, snippet in blocks[:max_results]:
        title = re.sub(r"<[^>]+>", "", title).strip()
        snippet = re.sub(r"<[^>]+>", "", snippet).strip()
        if title:
            results.append({"title": title, "snippet": snippet, "url": url})

    if not results:
        return ""

    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['title']}")
        if r["snippet"]:
            lines.append(f"    {r['snippet']}")
        lines.append(f"    来源: {r['url']}")
        lines.append("")

    return "\n".join(lines)
