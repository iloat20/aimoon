"""Tests for the business_moat tool (Task 8)."""
from __future__ import annotations

from aimoon.adapters.driven.ai.tools.business_moat import run
from aimoon.core.domain.entities.financial import FinancialData
from aimoon.core.domain.entities.research import ResearchReport, ResearchReportData


def _self_fin() -> FinancialData:
    return FinancialData(
        symbol="600519",
        report_period="2024-12-31",
        revenue=300.0,
        net_profit=60.0,
        equity=400.0,
        operating_cf=55.0,
    )


def _research() -> ResearchReportData:
    return ResearchReportData(
        symbol="600519",
        reports=[
            ResearchReport(
                title="贵州茅台深度研报",
                institution="中信证券",
                rating="买入",
                industry="白酒",
                pe_this_yr=30.0,
            )
        ],
        source="akshare",
    )


def _history_ocf_none() -> list[FinancialData]:
    return []


def test_happy_path_returns_swot_moat_and_bargaining() -> None:
    out = run(_self_fin(), _research(), [], _history_ocf_none())

    assert "__partial__" not in out
    swot = out["swot"]
    assert "strengths" in swot and "weaknesses" in swot
    assert "opportunities" in swot and "risks" in swot
    assert isinstance(swot["strengths"], list)
    assert isinstance(swot["weaknesses"], list)
    # 护城河来源必为允许的枚举子集
    for src in out["moat_sources"]:
        assert src in {"brand", "channel", "cost", "network_effect", "patent"}
    assert isinstance(out["ocf_quality"], (int, float))
    assert isinstance(out["upstream_bargaining"], (int, float))
    assert isinstance(out["downstream_bargaining"], (int, float))


def test_partial_when_self_fin_missing() -> None:
    out = run(None, _research(), [], _history_ocf_none())
    assert out["__partial__"] == "missing_self_fin"


def test_partial_when_research_missing() -> None:
    out = run(_self_fin(), None, [], _history_ocf_none())
    assert out["__partial__"] == "missing_research"


def test_partial_when_social_posts_not_supplied() -> None:
    # 入参为 None → 缺失降级
    out = run(_self_fin(), _research(), None, _history_ocf_none())  # type: ignore[arg-type]
    assert out["__partial__"] == "missing_social_posts"


def test_partial_when_history_ocf_missing() -> None:
    out = run(_self_fin(), _research(), [], None)  # type: ignore[arg-type]
    assert out["__partial__"] == "missing_history_ocf"
