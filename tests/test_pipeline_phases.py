"""Pipeline v2 phase-level tests (Tasks 1-4 + 13). Per-task TDD."""

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


def test_two_phases_defined():
    assert len(Phase) == 2
    assert Phase.ANALYSIS.value == "analysis"
    assert Phase.COMPILE.value == "compile"


def test_pipeline_specs_have_required_fields():
    for spec in get_pipeline_phases():
        assert spec.system_prompt_template
        assert spec.timeout_sec > 0


# ---- Task 13 ----


class _FakeSettings:
    deepseek_model = "deepseek-v4-flash"
    deepseek_temperature = 0.3
    deepseek_max_tokens = 1024


class _FakeAnalyzer:
    """Minimal analyzer shim satisfying PipelineOrchestrator.AnalyzerRuntime."""

    def __init__(self) -> None:
        self._settings = _FakeSettings()
        self._provided_settings = None
        self.api_url = "http://fake"
        self.api_key = "fake"
        self._http = None

    def _build_data_dict(self, info, reports=None, financial_md_path=None):
        return {"symbol": info.symbol, "name": info.name, "_fake": True}

    async def _stream_final_response(self, messages):
        return "[compiled fake markdown]"

    async def _stream_final_response(self, messages):
        return "[compiled fake markdown]"


@pytest.fixture
def _fake_analyzer(monkeypatch):
    fake = _FakeAnalyzer()

    async def _fake_call_llm(self, messages, *, max_tokens=None, reasoning_effort="max"):
        return {"role":"assistant","content":"# 分析草稿\n\n(fake draft content)"}

    async def _fake_stream_llm(self, messages, *, max_tokens=None, reasoning_effort="max"):
        return "[compiled fake markdown]"

    def _fake_peer_compare_module(si, search_fn):
        return {"_fake": True, "tool": "peer_compare"}

    monkeypatch.setattr(PipelineOrchestrator, "_call_llm_with_stream", _fake_call_llm)
    monkeypatch.setattr(PipelineOrchestrator, "_stream_llm", _fake_stream_llm)
    import aimoon.adapters.driven.ai.pipeline.orchestrator as _orch_mod
    monkeypatch.setattr(_orch_mod, "_run_peer_compare", _fake_peer_compare_module)
    return fake


@pytest.mark.asyncio
async def test_orchestrator_runs_two_phases(_fake_analyzer):
    """Two-phase pipeline (ANALYSIS + COMPILE) completes without raising."""
    ctx = await PipelineOrchestrator(_fake_analyzer).run(StockAnalysis(symbol="000001"))
    assert isinstance(ctx, dict)
    # ANALYSIS + COMPILE 两阶段都登记在 phase_results 里
    assert "analysis" in ctx["phase_results"]
    assert "compile" in ctx["phase_results"]
