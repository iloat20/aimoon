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


def _fin_temporal(ocf: float = 6.0e10, investing_cf: float = -3e9) -> dict:
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
    # 每股目标价为 dict({price, pe, probability}),不再是裸浮点(避免总价值误标为每股价)。
    neutral = targets["neutral"]
    assert isinstance(neutral, dict) and "price" in neutral
    assert neutral["price"] > 0
    # 保守 < 中性 < 乐观(按每股目标价排序)
    assert targets["conservative"]["price"] <= targets["neutral"]["price"] <= targets["optimistic"]["price"]
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
    quote = _quote(price=10.0, pe=5.0, pb=1.0)
    # OCF 极小 + 投资净流出 → base_fcfe<0 触发 DDM 回退;给分红数据走正目标价分支。
    out = run(ft, quote, {}, _financial_with_dividend(1.0e8))
    assert "__partial__" not in out
    assert math.isfinite(out["fcfe_targets"]["neutral"]["price"])


def _financial_with_dividend(dividend_paid: float):
    class _Fin:
        pass

    _Fin.dividend_paid = dividend_paid
    return _Fin()


def test_ddm_fallback_when_fcfe_negative() -> None:
    """投资现金流净流出远超 OCF → base_fcfe<0 → DCF 退化,回退 DDM 出正目标价。"""
    # 格力式:OCF 463.8 亿,投资净流出 486 亿(capex 代理),base_fcfe 为负。
    ft = _fin_temporal(ocf=463.8e8, investing_cf=-486e8)
    ft["revenue_cagr"] = -0.086
    quote = _quote(price=38.36, pe=7.36, pb=1.8)
    quote.market_cap = 55.5e8 * 38.36  # shares * price
    financial = _financial_with_dividend(181.7e8)
    out = run(ft, quote, None, financial)

    assert out.get("valuation_method") == "ddm_fallback"
    targets = out["fcfe_targets"]
    for tier in ("conservative", "neutral", "optimistic"):
        assert targets[tier]["price"] is not None and targets[tier]["price"] > 0
    # 三档应为正且保守 < 中性 < 乐观
    assert (
        targets["conservative"]["price"]
        <= targets["neutral"]["price"]
        <= targets["optimistic"]["price"]
    )
    # 目标价应接近现价(38.36),不应再出现负值垃圾。
    assert 20 < targets["neutral"]["price"] < 60


def test_ddm_fallback_returns_na_when_no_dividend() -> None:
    """FCFE 为负且无分红数据 → 三档全 None(渲染为 N/A),不再输出负值。"""
    ft = _fin_temporal(ocf=463.8e8, investing_cf=-486e8)
    quote = _quote(price=38.36, pe=7.36, pb=1.8)
    quote.market_cap = 55.5e8 * 38.36
    out = run(ft, quote, None, _financial_with_dividend(0.0))

    assert out.get("valuation_method") == "ddm_fallback"
    targets = out["fcfe_targets"]
    for tier in ("conservative", "neutral", "optimistic"):
        assert targets[tier]["price"] is None
