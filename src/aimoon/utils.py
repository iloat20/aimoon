"""Legacy compatibility layer — re-exports from shared/ package.

Prefer importing from `aimoon.shared.*` directly.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

from .shared.symbols import (  # noqa: F401 — re-export
    resolve_market,
    resolve_symbol,
    to_eastmoney_market,
    to_sina_symbol,
    to_xueqiu_symbol,
)


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
    import re as _re

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


@contextmanager
def silent_failure(context: str, default_return=None):
    try:
        yield
    except Exception as e:
        _exc = type(e).__name__
        if _exc in (
            "ConnectionError",
            "ConnectionAbortedError",
            "RemoteDisconnected",
            "TimeoutError",
            "OSError",
        ):
            logging.debug("[%s] %s: %s", context, _exc, e)
        else:
            logging.warning("[%s] %s: %s", context, _exc, e)


def retry_on_connection(
    func, *args, retries: int = 2, delay: float = 1.0, **kwargs
):
    """Call *func* with retries on transient connection errors."""
    import time

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return func(*args, **kwargs)
        except (
            ConnectionError,
            ConnectionAbortedError,
            TimeoutError,
            OSError,
        ) as exc:
            last_exc = exc
            if attempt < retries:
                logging.debug(
                    "[retry] %s attempt %d/%d failed: %s",
                    func.__qualname__,
                    attempt + 1,
                    retries,
                    exc,
                )
                time.sleep(delay * (attempt + 1))
    raise last_exc  # type: ignore[misc]
