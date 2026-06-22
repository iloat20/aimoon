"""pysnowball adapter for fetching financial statement data.

Fetches balance sheet, income statement, and key indicators from Xueqiu.
Data format: each value is [amount, yoy_ratio], e.g. [3199亿, 0.024].
"""

from __future__ import annotations

from datetime import datetime

from ..config.settings import get_settings
from ..models.stock import FinancialData
from ..utils import to_xueqiu_symbol


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
        ts: int = int(float(str(ms)))
        if ts > 1_000_000_000_000:  # milliseconds
            ts //= 1000
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

    async def fetch_capital_flow(self, symbol: str) -> dict:
        """Fetch capital flow data via pysnowball capital_flow / capital_history.

        Returns a flat dict with keys:
            main_net_today, main_net_5d,
            super_large_net, large_net, medium_net, small_net.
        Returns empty dict on failure.
        """
        self._ensure_init()
        result: dict = {}

        try:
            result = self._fetch_capital_flow_impl(symbol)
        except Exception:
            pass

        return result

    def _fetch_capital_flow_impl(self, symbol: str) -> dict:
        import pysnowball as ball

        xq_symbol = to_xueqiu_symbol(symbol)
        result: dict = {}

        # --- Today's capital assortment (超大/大/中/小单) ---
        try:
            assort_raw = ball.capital_assort(xq_symbol)
            assort = assort_raw.get("data", {}) if isinstance(assort_raw, dict) else {}
            if assort:
                # Xueqiu returns buy/sell per size
                buy_large = float(assort.get("buy_large", 0) or 0)
                sell_large = float(assort.get("sell_large", 0) or 0)
                buy_medium = float(assort.get("buy_medium", 0) or 0)
                sell_medium = float(assort.get("sell_medium", 0) or 0)
                buy_small = float(assort.get("buy_small", 0) or 0)
                sell_small = float(assort.get("sell_small", 0) or 0)

                # Net = buy - sell
                result["large_net"] = buy_large - sell_large
                result["medium_net"] = buy_medium - sell_medium
                result["small_net"] = buy_small - sell_small

                # Super large = buy_total - sell_total - large - medium - small
                buy_total = float(assort.get("buy_total", 0) or 0)
                sell_total = float(assort.get("sell_total", 0) or 0)
                result["super_large_net"] = (
                    buy_total - sell_total
                    - result["large_net"]
                    - result["medium_net"]
                    - result["small_net"]
                )

                # Total main force = super + big
                result["main_net_today"] = (
                    result["super_large_net"] + result["large_net"]
                )
        except Exception:
            pass

        # --- Historical flow (近5日) ---
        try:
            hist_raw = ball.capital_history(xq_symbol, count=6)
            hist_data = (
                hist_raw.get("data", {})
                if isinstance(hist_raw, dict)
                else {}
            )

            # Use sum5 directly if available
            sum5 = hist_data.get("sum5")
            if sum5 is not None:
                result["main_net_5d"] = float(sum5)
            else:
                hist_items = hist_data.get("items", [])
                if isinstance(hist_items, list) and len(hist_items) >= 2:
                    # Sum the last 5 days (excluding today)
                    net_sum = 0.0
                    for item in hist_items[-6:-1]:
                        net_val = item.get("amount", 0) or 0
                        net_sum += float(net_val)
                    result["main_net_5d"] = net_sum
                elif not result.get("main_net_5d"):
                    result["main_net_5d"] = 0.0
        except Exception:
            result.setdefault("main_net_5d", 0.0)

        # Ensure all keys exist
        for k in (
            "main_net_today",
            "main_net_5d",
            "super_large_net",
            "large_net",
            "medium_net",
            "small_net",
        ):
            result.setdefault(k, 0.0)

        return result

    def _fetch_via_pysnowball(self, symbol: str) -> FinancialData:
        import pysnowball as ball

        xq_symbol = to_xueqiu_symbol(symbol)
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
                result.equity = _val(latest, "total_assets") - _val(
                    latest, "total_liab"
                )
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
