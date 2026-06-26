"""Stock symbol resolution and market code conversion utilities."""

from __future__ import annotations

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


def to_eastmoney_market(symbol: str) -> str:
    """Convert to East Money market code: '1' for SH, '0' for SZ/BJ."""
    return "1" if symbol.startswith("6") else "0"
