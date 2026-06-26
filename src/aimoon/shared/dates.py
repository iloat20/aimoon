"""Shared date/time formatting utilities."""

from __future__ import annotations

from datetime import datetime

FMT_DATETIME = "%Y-%m-%d %H:%M:%S"
FMT_DATE = "%Y-%m-%d"
FMT_COMPACT = "%Y%m%d"
FMT_TIMESTAMP = "%Y%m%d_%H%M%S"


def now_str(fmt: str = FMT_DATETIME) -> str:
    """Current time formatted string."""
    return datetime.now().strftime(fmt)


def now_iso() -> str:
    """Current time as ISO 8601 string with local timezone."""
    return datetime.now().astimezone().isoformat()
