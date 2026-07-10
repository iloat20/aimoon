"""_unwrap count 覆盖测试 — 年报/季报/资金流成功时显示 1 而非 0。"""

from __future__ import annotations

from aimoon.adapters.driven.collectors.orchestrator import CollectorOrchestrator
from aimoon.core.domain.entities.capital_flow import CapitalFlowData
from aimoon.core.domain.entities.financial import FinancialData


def _unwrap_count(orch, result, factory, platform, ok, count):
    results: list = []
    orch._unwrap(
        result, factory, symbol="600519", platform=platform,
        ok=ok, msg=lambda d: "", fail="fail", results=results, count=count,
    )
    return results[0]


def test_financial_success_count_is_one():
    orch = CollectorOrchestrator()
    data = FinancialData(symbol="600519")
    data.report_period = "2025-12-31"
    r = _unwrap_count(
        orch, data, FinancialData, "财务数据(年报)",
        ok=lambda d: d and d.report_period,
        count=lambda d: 1 if (d and d.report_period) else 0,
    )
    assert r.status == "success"
    assert r.count == 1


def test_financial_empty_count_is_zero():
    orch = CollectorOrchestrator()
    data = FinancialData(symbol="600519")  # 无 report_period
    r = _unwrap_count(
        orch, data, FinancialData, "财务数据(年报)",
        ok=lambda d: d and d.report_period,
        count=lambda d: 1 if (d and d.report_period) else 0,
    )
    assert r.status == "empty"
    assert r.count == 0


def test_capital_flow_success_count_is_one():
    orch = CollectorOrchestrator()
    data = CapitalFlowData(symbol="600519")
    data.source = "akshare"
    r = _unwrap_count(
        orch, data, CapitalFlowData, "资金流向",
        ok=lambda d: d and d.source and d.source != "all_failed",
        count=lambda d: 1 if (d and d.source and d.source != "all_failed") else 0,
    )
    assert r.status == "success"
    assert r.count == 1


def test_capital_flow_all_failed_count_is_zero():
    orch = CollectorOrchestrator()
    data = CapitalFlowData(symbol="600519")
    data.source = "all_failed"
    r = _unwrap_count(
        orch, data, CapitalFlowData, "资金流向",
        ok=lambda d: d and d.source and d.source != "all_failed",
        count=lambda d: 1 if (d and d.source and d.source != "all_failed") else 0,
    )
    assert r.status == "empty"
    assert r.count == 0
