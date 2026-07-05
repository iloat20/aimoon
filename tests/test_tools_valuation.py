"""Tests for the valuation tool (Task 10)."""
from __future__ import annotations

import math

import pytest

from aimoon.adapters.driven.ai.tools.valuation import run
from aimoon.core.domain.entities.quote import StockQuote


def _quote(price: float = 1500.0, pe: float = 30.0, pb: float = 9.0) -> StockQuote:
    return StockQuote(
        symbol="600519",
        name="贵州茅台",
        price=price,
        pe=pe,
        pb=pb,
        market_cap=1.88e12,
    )


def _fin_temporal(ocf: float = 55.0, investing_cf: float = 0.0) -> dict:
    return {
        "n_years": 3,
        "years": [
            {"period": "2024-12-31", "operating_cf": ocf, "investing_cf": investing_cf},
        ],
        "revenue_cagr": 0.15,
        "net_profit_cagr": 0.16,
        "roe_trend": [0.25, 0.24, 0.23],
        "ocf_profit_ratio": 0.92,
        "ocf_partial": False,
        "break_points": [],
    }


def test_happy_path_pe_pb_fcfe_and_peer_comp() -> None:
    peer_comp = {
        "peers": [
            {"name": "五粮液", "pe": 25.0},
            {"name": "泸州老窖", "pe": 28.0},
            {"name": "山西汾酒", "pe": 32.0},
        ],
        "industry": "白酒",
    }
    out = run(_fin_temporal(), _quote(), peer_comp)

    assert "__partial__" not in out
    assert out["pe"] == pytest.approx(30.0, abs=1e-3)  # type: ignore[union-attr]
    assert out["pb"] == pytest.approx(9.0, abs=1e-3)  # type: ignore[union-attr]
    targets = out["fcfe_targets"]
    assert {"conservative", "neutral", "optimistic"}.issubset(set(targets.keys()))
    assert targets["conservative"] > 0
    # 保守 < 中性 < 乐观
    assert targets["conservative"] <= targets["neutral"] <= targets["optimistic"]
    assumptions = out["fcfe_assumptions"]
    assert "growth" in assumptions and "discount_rate" in assumptions and "years" in assumptions
    assert 0 < assumptions["discount_rate"] <= 0.12
    # peer comparison
    pc = out["peer_comparison"]
    assert isinstance(pc, list) and len(pc) >= 3


def test_partial_when_fin_temporal_missing() -> None:
    out = run(None, _quote(), {})
    assert out["__partial__"] == "missing_fin_temporal"


def test_partial_when_quote_missing() -> None:
    out = run(_fin_temporal(), None, {})
    assert out["__partial__"] == "missing_quote"


def test_partial_when_ocf_missing() -> None:
    ft = _fin_temporal(ocf=0.0)
    ft["ocf_partial"] = True
    out = run(ft, _quote(), {})
    assert out["__partial__"] == "missing_ocf"


def test_finite_even_with_low_numbers() -> None:
    ft = _fin_temporal(ocf=1.0)
    out = run(ft, _quote(price=10.0, pe=5.0, pb=1.0), {})
    assert "__partial__" not in out
    assert math.isfinite(out["fcfe_targets"]["neutral"])
