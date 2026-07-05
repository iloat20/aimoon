"""Tests for the financial_temporal tool (Task 6)."""
from __future__ import annotations

import pytest

from aimoon.adapters.driven.ai.tools.financial_temporal import run
from aimoon.core.domain.entities.financial import FinancialData


def _fin(
    period: str,
    revenue: float = 100.0,
    net_profit: float = 20.0,
    equity: float = 200.0,
    operating_cf: float | None = 18.0,
    revenue_yoy: float = 0.1,
    net_profit_yoy: float = 0.12,
) -> FinancialData:
    return FinancialData(
        symbol="600519",
        report_period=period,
        revenue=revenue,
        revenue_yoy=revenue_yoy,
        net_profit=net_profit,
        net_profit_yoy=net_profit_yoy,
        equity=equity,
        operating_cf=operating_cf if operating_cf is not None else 0.0,
    )


def test_happy_path_three_years_computes_cagr_and_years() -> None:
    history = [
        _fin("2024-12-31", revenue=300.0, net_profit=60.0, equity=400.0, operating_cf=55.0),
        _fin("2023-12-31", revenue=250.0, net_profit=50.0, equity=350.0, operating_cf=45.0),
        _fin("2022-12-31", revenue=200.0, net_profit=40.0, equity=300.0, operating_cf=35.0),
    ]
    out = run(history)

    assert "__partial__" not in out
    assert out["n_years"] == 3
    assert len(out["years"]) == 3
    # revenue CAGR: (300/200)^0.5 - 1 ~= 0.2247
    assert out["revenue_cagr"] == pytest.approx(0.2247, abs=1e-3)
    # net_profit CAGR: (60/40)^0.5 - 1 ~= 0.2247
    assert out["net_profit_cagr"] == pytest.approx(0.2247, abs=1e-3)
    assert out["ocf_profit_ratio"] == pytest.approx(55.0 / 60.0, abs=1e-3)
    assert out["ocf_partial"] is False
    roe_trend = out["roe_trend"]
    assert len(roe_trend) == 3
    # 2024 roe = 60/400 = 0.15
    assert roe_trend[0] == pytest.approx(0.15, abs=1e-3)


def test_partial_when_empty_history() -> None:
    out = run([])
    assert out["__partial__"] == "no_history"


def test_partial_when_history_is_none() -> None:
    out = run(None)
    assert out["__partial__"] == "no_history"


def test_ocf_partial_when_operating_cf_missing() -> None:
    history = [
        _fin("2024-12-31", revenue=300.0, net_profit=60.0, equity=400.0, operating_cf=None),
        _fin("2023-12-31", revenue=250.0, net_profit=50.0, equity=350.0, operating_cf=None),
        _fin("2022-12-31", revenue=200.0, net_profit=40.0, equity=300.0, operating_cf=None),
    ]
    out = run(history)
    assert "__partial__" not in out
    assert out["ocf_partial"] is True
    assert out["ocf_profit_ratio"] == 0.0
    # revenue / profit CAGR 仍应正常计算
    assert out["revenue_cagr"] == pytest.approx(0.2247, abs=1e-3)


def test_negative_values_use_log_robust_cagr_fallback() -> None:
    history = [
        _fin("2024-12-31", revenue=200.0, net_profit=10.0),
        _fin("2023-12-31", revenue=100.0, net_profit=-5.0),
        _fin("2022-12-31", revenue=50.0, net_profit=-20.0),
    ]
    out = run(history)
    assert "__partial__" not in out
    # 负值场景不应抛异常,返回有限数值
    import math

    assert math.isfinite(out["revenue_cagr"])  # type: ignore[arg-type]
    assert math.isfinite(out["net_profit_cagr"])  # type: ignore[arg-type]


def test_two_years_still_computes_without_partial() -> None:
    history = [
        _fin("2024-12-31", revenue=300.0, net_profit=60.0, equity=400.0),
        _fin("2023-12-31", revenue=200.0, net_profit=40.0, equity=300.0),
    ]
    out = run(history)
    assert "__partial__" not in out
    assert out["n_years"] == 2
