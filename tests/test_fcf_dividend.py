"""Tests for fcf_dividend tool: degradation paths + normal FCF computation.

Audit P3.1 priority 3 — only degradation was covered. Adds real
normal-path coverage for the free-cash-flow / dividend model.
"""

from aimoon.adapters.driven.ai.tools.fcf_dividend import run
from aimoon.core.domain.entities.financial import FinancialData
from aimoon.core.domain.entities.quote import StockQuote

FIN_T = {
    "years": [
        {"operating_cf": 1.0e10, "investing_cf": -3.0e9},
        {"operating_cf": 9.0e9, "investing_cf": -2.5e9},
    ]
}

QUOTE = StockQuote(price=1700.0, market_cap=2.0e12)


def test_missing_fin_temporal_returns_partial():
    assert run(None, FinancialData(), QUOTE)["__partial__"] == "missing_fin_temporal"


def test_missing_quote_returns_partial():
    assert run(FIN_T, FinancialData(), None)["__partial__"] == "missing_quote"


def test_missing_ocf_returns_partial():
    assert run({"years": [{}]}, FinancialData(), QUOTE)["__partial__"] == "missing_ocf"


def test_normal_path_computes_fcf():
    fin = FinancialData(net_profit=5.0e9)
    out = run(FIN_T, fin, QUOTE)
    assert "__partial__" not in out
    # OCF = 1.0e10, capex = |-3.0e9| = 3.0e9 -> FCF = 7.0e9
    assert out["ocf"] == 1.0e10
    assert out["capex"] == 3.0e9
    assert out["fcf"] == 7.0e9
    assert out["fcf_margin"] is not None
    # dividend fields rely on `financial.statements`, a field FinancialData
    # does NOT declare -> they are N/A in real usage (known gap, see audit).
    assert out["payout_ratio"] is None
    assert out["dividend_yield"] is None
    assert out["fcf_cover"] is None
