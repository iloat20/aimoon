"""Common parsing utilities for infrastructure layer."""

from __future__ import annotations

import re as _re
from urllib.parse import unquote


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
    """Extract actual article URL from Toutiao jump link."""
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
        return unquote(m.group(1))
    return ""
