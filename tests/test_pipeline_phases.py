"""Pipeline v2 phase-level tests (Tasks 1-4). Per-task TDD."""

import pytest

from aimoon.adapters.driven.ai.pipeline import Phase, get_pipeline_phases
from aimoon.adapters.driven.ai.pipeline.orchestrator import PipelineOrchestrator
from aimoon.adapters.driven.financial.akshare_adapter import AkshareFinancialAdapter
from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
from aimoon.core.domain.entities.financial import FinancialData


def test_history_financial_defaults_to_empty():
    assert StockAnalysis(symbol="600519").history_financial == []


def test_history_financial_accepts_list():
    h = [
        FinancialData(symbol="600519", report_period="2024-12-31"),
        FinancialData(symbol="600519", report_period="2023-12-31"),
    ]
    agg = StockAnalysis(symbol="600519", history_financial=h)
    assert len(agg.history_financial) == 2


# ---- Task 2 ----


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fetch_history_returns_up_to_n_years():
    adapter = AkshareFinancialAdapter()
    result = await adapter.fetch_history("600519", years=3)
    assert isinstance(result, list) and 1 <= len(result) <= 3
    periods = [r.report_period for r in result if r.report_period]
    assert periods == sorted(periods, reverse=True)


@pytest.mark.asyncio
async def test_fetch_history_bad_symbol_returns_empty():
    assert await AkshareFinancialAdapter().fetch_history("999999", years=3) == []


# ---- Task 3 ----


@pytest.mark.integration
@pytest.mark.asyncio
async def test_collect_all_populates_history_financial():
    from aimoon.adapters.driven.collectors.composite_repo import CompositeStockAnalysisRepository

    repo = CompositeStockAnalysisRepository(financial_collector=AkshareFinancialAdapter())
    agg = await repo.collect_all("600519")
    assert isinstance(agg.history_financial, list) and len(agg.history_financial) >= 1
    assert agg.financial.report_period  # 旧字段仍在


# ---- Task 4 ----


def test_five_phases_defined():
    assert len(Phase) == 5
    assert Phase.PLAN.value == "plan"


def test_pipeline_specs_have_required_fields():
    for spec in get_pipeline_phases():
        assert spec.system_prompt_template
        assert spec.timeout_sec > 0


@pytest.mark.asyncio
async def test_orchestrator_runs_all_phases_placeholder():
    ctx = await PipelineOrchestrator(object()).run(StockAnalysis(symbol="000001"))
    assert isinstance(ctx, dict)
