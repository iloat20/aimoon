"""Shared utilities for symbol/market resolution and text parsing."""

from __future__ import annotations

import re as _re

_MARKET_MAP = {"6": "SH", "0": "SZ", "3": "SZ", "4": "BJ", "8": "BJ"}


def resolve_market(symbol: str) -> str:
    """Resolve stock code to market: SH/SZ/BJ."""
    return _MARKET_MAP.get(symbol[0], "SZ")


def resolve_symbol(raw: str) -> tuple[str, str, str]:
    """Resolve raw stock code to (symbol, market, name)."""
    symbol = raw.strip().zfill(6)
    market = resolve_market(symbol)
    return symbol, market, symbol


def to_sina_symbol(symbol: str) -> str:
    """Convert 6-digit code to Sina format: sh600519 or sz000001."""
    m = resolve_market(symbol)
    return f"{m.lower()}{symbol}"


def to_xueqiu_symbol(symbol: str) -> str:
    """Convert 6-digit code to Xueqiu format: SH600519."""
    return f"{resolve_market(symbol)}{symbol}"


def parse_chinese_count(txt: str) -> int:
    """Parse Chinese count strings like '1.2万', '3.5亿' to int."""
    if not txt:
        return 0
    txt = txt.strip().lower().replace(",", "")
    try:
        if "万" in txt or "w" in txt:
            num = txt.replace("万", "").replace("w", "").strip()
            return int(float(num) * 10000)
        if "亿" in txt or "b" in txt:
            num = txt.replace("亿", "").replace("b", "").strip()
            return int(float(num) * 100000000)
        return int(float(txt))
    except (ValueError, TypeError):
        return 0


def extract_toutiao_url(href: str) -> str:
    """Extract actual article URL from Toutiao jump link.

    Tries triple-encoded group ID patterns, then url parameter fallback.
    Returns empty string if no match found.
    """
    m = _re.search(r"group%252F(\d{15,})", href)
    if m:
        return f"https://www.toutiao.com/article/{m.group(1)}/"
    m = _re.search(r"group%2[5Ff](\d{15,})", href)
    if m:
        return f"https://www.toutiao.com/article/{m.group(1)}/"
    m = _re.search(r"group(?:%2F|=|/)(\d{15,})", href)
    if m:
        return f"https://www.toutiao.com/article/{m.group(1)}/"
    m = _re.search(r"url=([^&]+)", href)
    if m:
        from urllib.parse import unquote
        return unquote(m.group(1))
    return ""
