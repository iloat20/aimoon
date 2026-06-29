"""Data cleaning utilities for social media and search result snippets.

Extracts key signals (dates, numbers, entities) and removes noise
before feeding data to the analysis model.
"""

from __future__ import annotations

import re

_DATE_PATTERNS = [
    re.compile(r"\d{4}[-/年.]\d{1,2}[-/月.]\d{1,2}[日]?"),
    re.compile(r"\d{4}年\d{1,2}月"),
    re.compile(r"(?:今|昨|前天|大前天|今天|昨日)"),
]

_NUMBER_PATTERNS = [
    re.compile(r"-?\d+(?:\.\d+)?%"),  # percentages
    re.compile(r"-?\d+(?:\.\d+)?(?:亿|万亿)"),  # large amounts
    re.compile(r"-?\d+(?:\.\d+)?(?:万|千)"),  # medium amounts
    re.compile(
        r"(?:PE|PB|ROE|EPS|市值|营收|净利润|净利)[^\n]{0,5}(?:-?\d+(?:\.\d+)?)"
    ),  # financial metrics
]

_NOISE_PATTERNS = [
    re.compile(r"<[^>]+>"),  # HTML tags
    re.compile(r"返回搜狐.*$", re.MULTILINE),  # toutiao footer
    re.compile(r"打开.*?APP.*$", re.MULTILINE),  # app promos
    re.compile(r"(?:关注|点赞|收藏|转发|评论)\s*\d*", re.MULTILINE),
    re.compile(r"https?://\S+"),  # URLs
    re.compile(r"\[.*?\]"),  # bracket refs
    re.compile(r"^\s*[-=—]{3,}\s*$", re.MULTILINE),  # horizontal rules
    re.compile(r"^\s*[•·]\s*$", re.MULTILINE),  # bullet-only lines
]


def clean_snippet(text: str, max_len: int = 800) -> str:
    """Clean a single search result snippet.

    1. Strip HTML and noise patterns
    2. Extract lines containing dates or financial numbers
    3. Fall back to first N chars if no key signals found
    """
    if not text or text.strip() == "暂无数据":
        return ""

    t = text
    for pat in _NOISE_PATTERNS:
        t = pat.sub(" ", t)

    t = re.sub(r"\s+", " ", t).strip()

    lines = text.splitlines()
    scored: list[tuple[int, str]] = []
    for line in lines:
        clean_line = line.strip()
        if not clean_line or len(clean_line) < 5:
            continue
        score = 0
        for pat in _DATE_PATTERNS:
            if pat.search(clean_line):
                score += 2
                break
        for pat in _NUMBER_PATTERNS:
            if pat.search(clean_line):
                score += 3
                break
        if any(
            kw in clean_line
            for kw in (
                "营收",
                "净利",
                "利润",
                "增长",
                "下滑",
                "涨停",
                "跌停",
                "评级",
                "目标价",
                "风险",
                "机会",
                "龙头",
                "市占",
            )
        ):
            score += 1
        if score > 0:
            scored.append((score, clean_line))

    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        selected = [line for _, line in scored[:10]]
        result = "\n".join(selected)
    else:
        result = t

    if len(result) > max_len:
        result = result[:max_len] + "..."

    return result


def clean_social_texts(texts: dict[str, str]) -> dict[str, str]:
    """Clean all social platform texts before feeding to the model.

    Args:
        texts: mapping of platform_key -> raw text block

    Returns:
        cleaned mapping with same keys
    """
    return {key: clean_snippet(val) for key, val in texts.items()}
