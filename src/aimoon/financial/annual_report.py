"""Financial report fetcher and cache.

Fetches latest annual, semi-annual, and quarterly reports from cninfo.com.cn,
caches locally for AI analysis (30 days).
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from ..config.settings import get_settings

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

# Report type configs: (search keyword, display name)
_REPORT_TYPES = [
    ("年度报告", "年报"),
    ("半年度报告", "半年报"),
    ("季度报告", "季报"),
]


def _cache_path(symbol: str) -> Path:
    settings = get_settings()
    cache_dir = settings.cache_path / "financial_reports"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{symbol}.json"


def _load_cache(symbol: str) -> dict | None:
    path = _cache_path(symbol)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(data.get("cached_at", "2000-01-01"))
        cache_days = get_settings().financial_report_cache_days
        if (datetime.now() - cached_at).days > cache_days:
            return None
        return data
    except Exception:
        return None


def _save_cache(symbol: str, data: dict) -> None:
    path = _cache_path(symbol)
    data["cached_at"] = datetime.now().isoformat()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def _search_report(
    client: httpx.AsyncClient, symbol: str, keyword: str
) -> dict | None:
    """Search cninfo for a specific report type."""
    payload = {
        "stock": "",
        "pageNum": "1",
        "pageSize": "10",
        "tabKey": "fulltext",
        "category": "",
        "seDate": "",
        "searchkey": f"{symbol} {keyword}",
        "isHLtitle": "true",
        "sortName": "announcementTime",
        "sortType": "desc",
    }

    try:
        resp = await client.post(_CNINFO_URL, data=payload, headers=_HEADERS)
        if resp.status_code != 200:
            return None

        data = resp.json()
        items = data.get("announcements", [])

        for item in items:
            if item.get("secCode") != symbol:
                continue
            title_raw = item.get("announcementTitle", "")
            title = re.sub(r"<[^>]+>", "", title_raw).strip()
            if "摘要" in title or "英文" in title:
                continue
            if keyword in title:
                adjunct_url = item.get("adjunctUrl", "")
                if adjunct_url:
                    pdf_url = (
                        f"https://static.cninfo.com.cn/{adjunct_url}"
                    )
                else:
                    pdf_url = ""
                year_match = re.search(r"(\d{4})\s*年", title)
                year = year_match.group(1) if year_match else ""
                return {
                    "year": year,
                    "title": title[:100],
                    "pdf_url": pdf_url,
                }
    except Exception:
        pass
    return None


async def fetch_reports(symbol: str, force: bool = False) -> dict:
    """Fetch latest annual, semi-annual, and quarterly reports.

    Returns dict with keys:
        symbol, annual, semi_annual, quarterly, cached
    """
    if not force:
        cached = _load_cache(symbol)
        if cached:
            cached["cached"] = True
            return cached

    t0 = time.monotonic()
    result: dict[str, Any] = {
        "symbol": symbol,
        "annual": None,
        "semi_annual": None,
        "quarterly": None,
        "cached": False,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            for keyword, label in _REPORT_TYPES:
                report = await _search_report(client, symbol, keyword)
                if report:
                    if label == "年报":
                        result["annual"] = report
                    elif label == "半年报":
                        result["semi_annual"] = report
                    elif label == "季报":
                        result["quarterly"] = report
    except Exception:
        pass

    elapsed = (time.monotonic() - t0) * 1000

    # Log results
    reports_found = []
    if result["annual"]:
        reports_found.append(f"年报{result['annual']['year']}")
    if result["semi_annual"]:
        reports_found.append(f"半年报{result['semi_annual']['year']}")
    if result["quarterly"]:
        reports_found.append(f"季报{result['quarterly']['year']}")
    if reports_found:
        print(f"   报告获取: {', '.join(reports_found)} ({elapsed:.0f}ms)")
    else:
        print(f"   报告获取: 未找到 ({elapsed:.0f}ms)")

    _save_cache(symbol, result)
    return result
