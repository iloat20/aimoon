"""Stock symbol resolution and market code conversion - pure domain logic.

No external dependencies, pure functions based on mapping rules.
"""

from __future__ import annotations

_MARKET_MAP = {"6": "SH", "0": "SZ", "3": "SZ", "4": "BJ", "8": "BJ"}


def resolve_market(symbol: str) -> str:
    """Resolve stock code to market: SH/SZ/BJ."""
    if not symbol:
        raise ValueError("symbol 不能为空")
    market = _MARKET_MAP.get(symbol[0])
    if market is None:
        raise ValueError(f"无法识别的股票代码前缀: {symbol[0]} ({symbol})")
    return market


def resolve_symbol(raw: str) -> tuple[str, str, str]:
    """Resolve raw stock code to (symbol, market, name).

    name 字段为占位符，在无法获取真实股票名称时返回 symbol 本身。
    """
    if not raw or not raw.strip():
        raise ValueError("股票代码不能为空")
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
