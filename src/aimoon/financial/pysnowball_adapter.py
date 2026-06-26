"""pysnowball adapter for fetching financial statement data.

Fetches balance sheet, income statement, and key indicators from Xueqiu.
Data format: each value is [amount, yoy_ratio], e.g. [3199亿, 0.024].
"""

from __future__ import annotations

import asyncio

from ..config.settings import get_settings
from ..models.stock import FinancialData
from ..utils import silent_failure, to_xueqiu_symbol


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
    try:
        if isinstance(v, (list, tuple)):
            return float(v[0]) if len(v) > 0 else 0.0
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def _yoy(data: dict, key: str) -> float:
    """Extract YoY change ratio from [value, ratio] pair."""
    v = data.get(key, 0)
    if v is None:
        return 0.0
    if isinstance(v, (list, tuple)):
        if len(v) > 1 and v[1] is not None:
            return float(v[1]) * 100  # Convert to percentage
    return 0.0


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
            with silent_failure("pysnowball_set_token"):
                import pysnowball as ball

                ball.set_token(self._token)
        self._initialized = True

    async def fetch(self, symbol: str) -> FinancialData:
        self._ensure_init()
        try:
            return await asyncio.to_thread(self._fetch_via_pysnowball, symbol)
        except Exception as e:
            return FinancialData(symbol=symbol, source=f"pysnowball_failed: {e}")

    async def fetch_capital_flow(self, symbol: str) -> dict:
        """Fetch capital flow data via pysnowball capital_history.

        Returns a flat dict with keys:
            main_net_5d, net_3d, net_10d, net_20d.
        Returns empty dict on failure.
        """
        self._ensure_init()
        result: dict = {}

        with silent_failure("pysnowball_fetch_capital_flow"):
            result = self._fetch_capital_flow_impl(symbol)

        return result

    def _fetch_capital_flow_impl(self, symbol: str) -> dict:
        import pysnowball as ball

        xq_symbol = to_xueqiu_symbol(symbol)
        result: dict = {}

        # --- Historical flow (多周期累计) ---
        with silent_failure("pysnowball_capital_history"):
            hist_raw = ball.capital_history(xq_symbol, count=25)
            hist_data = (
                hist_raw.get("data", {})
                if isinstance(hist_raw, dict)
                else {}
            )

            # Use sum5 directly if available
            sum5 = hist_data.get("sum5")
            if sum5 is not None:
                result["main_net_5d"] = float(sum5)

            sum3 = hist_data.get("sum3")
            if sum3 is not None:
                result["net_3d"] = float(sum3)

            sum10 = hist_data.get("sum10")
            if sum10 is not None:
                result["net_10d"] = float(sum10)

            sum20 = hist_data.get("sum20")
            if sum20 is not None:
                result["net_20d"] = float(sum20)

        return result

    def _fetch_via_pysnowball(self, symbol: str) -> FinancialData:
        import pysnowball as ball

        xq_symbol = to_xueqiu_symbol(symbol)
        result = FinancialData(symbol=symbol, source="雪球(pysnowball)")

        # --- Balance Sheet ---
        with silent_failure("pysnowball_balance_sheet"):
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

        # --- Income Statement ---
        with silent_failure("pysnowball_income_statement"):
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

        # --- Cash Flow ---
        with silent_failure("pysnowball_cash_flow"):
            cf = ball.cash_flow(xq_symbol)
            cf_data = cf.get("data", {}) if isinstance(cf, dict) else {}
            items = cf_data.get("list", [])
            latest = _first(items)
            if latest:
                result.operating_cf = _val(latest, "ncf_from_oa")
                result.investing_cf = _val(latest, "ncf_from_ia")
                result.financing_cf = _val(latest, "ncf_from_fa")

        # --- Key Indicators (ROE, EPS, etc.) ---
        with silent_failure("pysnowball_indicators"):
            ind = ball.indicator(xq_symbol)
            ind_data = ind.get("data", {}) if isinstance(ind, dict) else {}
            items = ind_data.get("list", [])
            latest = _first(items)
            if latest:
                result.roe = _val(latest, "avg_roe")
                result.eps = _val(latest, "basic_eps")
                result.bvps = _val(latest, "np_per_share")

        return result
