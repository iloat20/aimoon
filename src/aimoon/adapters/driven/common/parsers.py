"""Common parsing utilities for infrastructure layer."""

from __future__ import annotations


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


def extract_toutiao_url(href: str) -> str:  # pragma: no cover - 头条源已移除, 死代码
    raise NotImplementedError("toutiao source removed")
