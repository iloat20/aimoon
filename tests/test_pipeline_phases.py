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


def test_five_phases_defined():
    assert len(Phase) == 5
    assert Phase.PLAN.value == "plan"


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

    def _build_data_dict(self, info, reports=None, financial_md_path=None):
        return {"symbol": info.symbol, "name": info.name, "_fake": True}


def _fake_llm_chat(self, messages, *, tools=None, tool_choice="auto"):
    """Plain-text response, no tool calls -> every phase finishes in 1 turn."""
    return {"role": "assistant", "content": f"[fake output for {len(messages)} messages]"}


@pytest.fixture
def _fake_analyzer(monkeypatch):
    monkeypatch.setattr(PipelineOrchestrator, "_llm_chat", _fake_llm_chat)
    return _FakeAnalyzer()


@pytest.mark.asyncio
async def test_orchestrator_runs_all_phases_placeholder(_fake_analyzer):
    """All 5 phases complete with a mocked LLM (no network)."""
    ctx = await PipelineOrchestrator(_fake_analyzer).run(StockAnalysis(symbol="000001"))
    assert isinstance(ctx, dict)
    assert set(ctx["phase_results"].keys()) == {
        "plan", "collect", "analysis", "self_check", "compile"
    }
    # SELF_CHECK wired in Task 14; COMPILE in Task 15 -> partial until then.
    assert "compile" in ctx["partial_phases"]


def test_parse_self_check_json_strips_fences():
    from aimoon.adapters.driven.ai.pipeline.orchestrator import _parse_self_check_json

    text = (
        '```json\n{"citations_ok": true, "tables_ok": true, "trigger_ok": true, '
        '"advice_ok": true, "norepeat_ok": false, "fixes_needed": ["x"]}\n```'
    )
    parsed, err = _parse_self_check_json(text)
    assert err is None
    assert parsed is not None
    assert parsed["norepeat_ok"] is False
    assert parsed["fixes_needed"] == ["x"]


def test_parse_self_check_json_invalid_returns_error():
    from aimoon.adapters.driven.ai.pipeline.orchestrator import _parse_self_check_json

    parsed, err = _parse_self_check_json("not json at all")
    assert parsed is None
    assert err is not None
