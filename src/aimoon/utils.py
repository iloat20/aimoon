"""Shared utilities for symbol/market resolution."""

from __future__ import annotations

_MARKET_MAP = {"6": "SH", "0": "SZ", "3": "SZ", "4": "BJ", "8": "BJ"}


def resolve_market(symbol: str) -> str:
    """Resolve stock code to market: SH/SZ/BJ."""
    return _MARKET_MAP.get(symbol[0], "SZ")


def to_sina_symbol(symbol: str) -> str:
    """Convert 6-digit code to Sina format: sh600519 or sz000001."""
    m = resolve_market(symbol)
    return f"{m.lower()}{symbol}"


def to_xueqiu_symbol(symbol: str) -> str:
    """Convert 6-digit code to Xueqiu format: SH600519."""
    return f"{resolve_market(symbol)}{symbol}"
