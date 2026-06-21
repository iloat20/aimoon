"""pysnowball adapter for fetching financial statement data.

Fetches balance sheet, income statement, and key indicators from Xueqiu.
Data format: each value is [amount, yoy_ratio], e.g. [3199亿, 0.024].
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..config.settings import get_settings
from ..models.stock import FinancialData


_MARKET_MAP = {"6": "SH", "0": "SZ", "3": "SZ", "4": "BJ", "8": "BJ"}


def _resolve_symbol(symbol: str) -> str:
    market = _MARKET_MAP.get(symbol[0], "SZ")
    return f"{market}{symbol}"


def _first(items: list | None) -> dict | None:
    """Get the first (latest) item from a list."""
    if items and len(items) > 0:
        return items[0]
    return None


def _val(data: dict, key: str) -> float:
    """Extract scalar value from [value, ratio] pair or plain number."""
    v = data.get(key, 0)
    if v is None:
        return 0.0
    if isinstance(v, (list, tuple)):
        return float(v[0]) if len(v) > 0 else 0.0
    return float(v)


def _yoy(data: dict, key: str) -> float:
    """Extract YoY change ratio from [value, ratio] pair."""
    v = data.get(key, 0)
    if v is None:
        return 0.0
    if isinstance(v, (list, tuple)):
        if len(v) > 1 and v[1] is not None:
            return float(v[1]) * 100  # Convert to percentage
    return 0.0


def _ts_to_str(ms: int | float | str | None) -> str:
    """Convert Unix timestamp (ms) to readable date string."""
    try:
        ts = int(float(str(ms)))
        if ts > 1e12:  # milliseconds
            ts /= 1000
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OSError):
        return str(ms) if ms else ""


class PysnowballAdapter:
    """Adapter for pysnowball financial data."""

    def __init__(self, token: str = "") -> None:
        settings = get_settings()
        self._token = token or settings.xueqiu_token
        self._initialized = False

    def _ensure_init(self) -> None:
        if self._initialized:
            return
        if self._token:
            try:
                import pysnowball as ball
                ball.set_token(self._token)
            except Exception:
                pass
        self._initialized = True

    async def fetch(self, symbol: str) -> FinancialData:
        self._ensure_init()
        try:
            return self._fetch_via_pysnowball(symbol)
        except Exception as e:
            return FinancialData(symbol=symbol, source=f"pysnowball_failed: {e}")

    def _fetch_via_pysnowball(self, symbol: str) -> FinancialData:
        import pysnowball as ball

        xq_symbol = _resolve_symbol(symbol)
        result = FinancialData(symbol=symbol, source="雪球(pysnowball)")

        # --- Balance Sheet ---
        try:
            bs = ball.balance(xq_symbol)
            bs_data = bs.get("data", {}) if isinstance(bs, dict) else {}
            items = bs_data.get("list", [])
            latest = _first(items)
            if latest:
                result.report_period = str(latest.get("report_name", ""))
                result.total_assets = _val(latest, "total_assets")
                result.total_liabilities = _val(latest, "total_liab")
                # equity = total_assets - total_liabilities
                result.equity = _val(latest, "total_assets") - _val(latest, "total_liab")
        except Exception:
            pass

        # --- Income Statement ---
        try:
            inc = ball.income(xq_symbol)
            inc_data = inc.get("data", {}) if isinstance(inc, dict) else {}
            items = inc_data.get("list", [])
            latest = _first(items)
            if latest:
                if not result.report_period:
                    result.report_period = str(latest.get("report_name", ""))
                result.revenue = _val(latest, "total_revenue")
                result.net_profit = _val(latest, "net_profit")
                result.revenue_yoy = _yoy(latest, "total_revenue")
                result.net_profit_yoy = _yoy(latest, "net_profit")
        except Exception:
            pass

        # --- Cash Flow ---
        try:
            cf = ball.cash_flow(xq_symbol)
            cf_data = cf.get("data", {}) if isinstance(cf, dict) else {}
            items = cf_data.get("list", [])
            latest = _first(items)
            if latest:
                result.operating_cf = _val(latest, "ncf_from_oa")
                result.investing_cf = _val(latest, "ncf_from_ia")
                result.financing_cf = _val(latest, "ncf_from_fa")
        except Exception:
            pass

        # --- Key Indicators (ROE, EPS, etc.) ---
        try:
            ind = ball.indicator(xq_symbol)
            ind_data = ind.get("data", {}) if isinstance(ind, dict) else {}
            items = ind_data.get("list", [])
            latest = _first(items)
            if latest:
                result.roe = _val(latest, "avg_roe")
                result.eps = _val(latest, "basic_eps")
                result.bvps = _val(latest, "np_per_share")
        except Exception:
            pass

        return result
