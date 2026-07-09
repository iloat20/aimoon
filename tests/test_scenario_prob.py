"""Tests for scenario_prob tool: degradation paths + normal probability weighting.

Audit P3.1 priority 3 — only degradation was covered (via orchestrator
wiring mocks). This adds real normal-path coverage.
"""

from aimoon.adapters.driven.ai.tools.scenario_prob import run
from aimoon.core.domain.entities.quote import StockQuote

VAL = {
    "fcfe_targets": {
        "conservative": {"price": 1500.0, "pe": 28.0},
        "neutral": {"price": 1700.0, "pe": 32.0},
        "optimistic": {"price": 2000.0, "pe": 38.0},
    }
}

FIN = {
    "revenue_cagr": 0.12,
    "ocf_profit_ratio": 0.95,
    "roe_trend": [0.26, 0.25, 0.24],
    "years": [{"pe": 30.0}, {"pe": 28.0}, {"pe": 25.0}],
}

QUOTE = StockQuote(price=1700.0, pe=32.0)


def test_missing_valuation_returns_partial():
    assert run(None, QUOTE, FIN)["__partial__"] == "missing_valuation"


def test_missing_quote_returns_partial():
    assert run(VAL, None, FIN)["__partial__"] == "missing_quote"


def test_missing_targets_returns_partial():
    assert run({"foo": 1}, QUOTE, FIN)["__partial__"] == "missing_targets"


def test_zero_price_returns_partial():
    q = StockQuote(price=0.0, pe=32.0)
    assert run(VAL, q, FIN)["__partial__"] == "missing_price"


def test_normal_path_produces_weighted_targets():
    out = run(VAL, QUOTE, FIN)
    assert "__partial__" not in out
    tiers = out["targets"]
    assert set(tiers) == {"conservative", "neutral", "optimistic"}
    # probabilities sum to ~100%
    total = sum(t["probability"] for t in tiers.values())
    assert 99.0 <= total <= 101.0
    assert out["expected_target"] > 0  # weighted across tiers
    assert out["current_price"] == 1700.0
    # neutral downside is zero (price == neutral target) -> no risk/reward ratio
    assert out["downside_neutral_pct"] == 0.0
