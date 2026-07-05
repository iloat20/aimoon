"""Tests for the risk_quant tool (Task 9).

关键强制契约:bears 必须 ≥3 且每条含非空 trigger_condition(SELF_CHECK 来源)。
"""
from __future__ import annotations

from aimoon.adapters.driven.ai.tools.risk_quant import run
from aimoon.core.domain.entities.quote import StockQuote


def _quote(price: float = 100.0, pe: float = 25.0, pb: float = 5.0) -> StockQuote:
    return StockQuote(symbol="600519", name="贵州茅台", price=price, pe=pe, pb=pb)


def _fin_temporal_over(
    revenue_cagr: float, net_profit_cagr: float, roe_list: list[float]
) -> dict:
    return {
        "n_years": len(roe_list),
        "years": [],
        "revenue_cagr": revenue_cagr,
        "net_profit_cagr": net_profit_cagr,
        "roe_trend": roe_list,
        "ocf_profit_ratio": 0.9,
        "ocf_partial": False,
        "break_points": [],
    }


def test_happy_path_bears_at_least_three_with_triggers() -> None:
    # ROE 持续压缩 → 至少一条来自 ROE 压缩,再叠加估值偏高
    ft = _fin_temporal_over(0.05, 0.04, [0.30, 0.25, 0.18])
    out = run(ft, _quote(pe=55.0, pb=12.0))

    assert "__partial__" not in out
    bears = out["bears"]
    assert len(bears) >= 3, f"bears 必须 ≥3 条,实际 {len(bears)}"
    for b in bears:
        assert "theme" in b and b["theme"]
        assert "trigger_condition" in b and b["trigger_condition"], (
            f"每条 bear 必须有非空 trigger_condition: {b}"
        )
        assert "impact_pct" in b
    # bulls 同样应有内容
    assert "bulls" in out and isinstance(out["bulls"], list)
    alerts = out["ratio_alerts"]
    for key in ("goodwill_warn", "receivables_warn", "inventory_warn"):
        assert key in alerts
        assert isinstance(alerts[key], bool)


def test_bears_robust_even_with_few_signals() -> None:
    # 温和正常财务:bears 仍应 ≥3(含估值与行业基准兜底)
    ft = _fin_temporal_over(0.12, 0.10, [0.22, 0.21, 0.20])
    out = run(ft, _quote(pe=20.0, pb=4.0))
    assert len(out["bears"]) >= 3
    for b in out["bears"]:
        assert b["trigger_condition"]


def test_partial_when_fin_temporal_missing() -> None:
    out = run(None, _quote())
    assert out["__partial__"] == "missing_fin_temporal"


def test_partial_when_quote_missing() -> None:
    ft = _fin_temporal_over(0.1, 0.1, [0.2, 0.2, 0.2])
    out = run(ft, None)
    assert out["__partial__"] == "missing_quote"


def test_partial_when_empty_fin_temporal() -> None:
    out = run({}, _quote())
    assert out["__partial__"] == "insufficient_signals"


def test_declining_cagr_produces_revenue_profit_bear() -> None:
    ft = _fin_temporal_over(-0.05, -0.10, [0.18, 0.16, 0.14])
    out = run(ft, _quote(pe=30.0, pb=6.0))
    themes = " ".join(b["theme"] for b in out["bears"])
    assert "营收" in themes or "净利" in themes or "下滑" in themes or "CAGR" in themes
