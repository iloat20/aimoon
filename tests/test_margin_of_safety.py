"""Tests for the margin_of_safety tool (replaces the old valuation three-tier tool)."""
from __future__ import annotations

import pytest

from aimoon.adapters.driven.ai.pipeline.table_renderer import render_margin_of_safety
from aimoon.adapters.driven.ai.tools.margin_of_safety import run
from aimoon.core.domain.entities.financial import FinancialData
from aimoon.core.domain.entities.quote import StockQuote


def _quote(price: float = 39.81, pe: float = 7.64, pb: float = 1.8) -> StockQuote:
    return StockQuote(
        symbol="000651",
        name="格力电器",
        price=price,
        pe=pe,
        pb=pb,
        market_cap=price * 55.6e8,  # 流通股本代理 ≈ 55.6 亿股
    )


def _fin(
    net_profit: float = 288.6e8,
    monetary_funds: float = 1105.5e8,
) -> FinancialData:
    return FinancialData(net_profit=net_profit, monetary_funds=monetary_funds)


def _peers() -> dict:
    return {
        "peers": [
            {"name": "美的集团", "pe": 14.25},
            {"name": "海尔智家", "pe": 12.10},
            {"name": "海信家电", "pe": 10.40},
        ],
        "industry": "白色家电",
    }


def test_happy_path_safety_metrics() -> None:
    out = run({}, _quote(), _peers(), _fin())

    assert "__partial__" not in out
    assert out["pe"] == pytest.approx(7.64)
    assert out["pb"] == pytest.approx(1.8)
    # 净现金调整 PE = (市值 - 货币资金) / 净利润
    # 市值 = 39.81 * 55.6e8 ≈ 2213e8; (2213 - 1105.5) / 288.6 ≈ 3.84
    assert out["net_cash_pe"] == pytest.approx(3.84, abs=0.02)
    # 同业 PE 中位数 = median(14.25, 12.10, 10.40) = 12.10
    assert out["peer_pe_median"] == pytest.approx(12.10, abs=0.01)
    # 两档压力测试
    stress = out["stress"]
    assert len(stress) == 2
    assert stress[0]["drop"] == 30.0
    assert stress[1]["drop"] == 50.0
    # 净利 -30% → 约 202 亿; -50% → 约 144 亿
    assert stress[0]["net_profit"] == pytest.approx(202.02, abs=0.01)
    assert stress[1]["net_profit"] == pytest.approx(144.30, abs=0.01)
    # 下行空间为负且随跌幅单调加深(恒定 PE 下 ≈ -跌幅, 允许浮点误差)
    assert stress[0]["downside_pct"] == pytest.approx(-30.0, abs=1.0)
    assert stress[1]["downside_pct"] == pytest.approx(-50.0, abs=1.0)
    assert stress[1]["downside_pct"] < stress[0]["downside_pct"]


def test_net_cash_pe_none_when_monetary_funds_missing() -> None:
    out = run({}, _quote(), _peers(), FinancialData(net_profit=288.6e8))
    assert out["net_cash_pe"] is None
    assert out["stress"]  # 压力测试不依赖货币资金


def test_stress_empty_when_pe_missing() -> None:
    # pe=0 → 无法算 EPS/股价 → 压力测试为空
    q = _quote(pe=0.0)
    out = run({}, q, _peers(), _fin())
    assert out["stress"] == []


def test_partial_when_quote_missing() -> None:
    out = run({}, None, _peers(), _fin())
    assert out["__partial__"] == "missing_quote"


def test_peer_pe_median_ignores_zero() -> None:
    # 含一个 pe<=0 的脏数据,中位数应基于有效值
    peers = {
        "peers": [
            {"name": "A", "pe": 0.0},
            {"name": "B", "pe": 8.0},
            {"name": "C", "pe": 12.0},
        ]
    }
    out = run({}, _quote(), peers, _fin())
    assert out["peer_pe_median"] == pytest.approx(10.0, abs=0.01)


def test_render_margin_of_safety() -> None:
    out = run({}, _quote(), _peers(), _fin())
    md = render_margin_of_safety(out)
    assert "## 估值安全边际" in md
    assert "净现金调整 PE" in md
    assert "同业 PE 中位数" in md
    assert "压力测试" in md
    assert "净利 −30%" in md
    assert "净利 −50%" in md


def test_render_margin_of_safety_empty_on_missing() -> None:
    # pe/pb 均为 0 → 返回空串
    md = render_margin_of_safety({"pe": 0, "pb": 0})
    assert md == ""
