"""Tests for AI pipeline v2 -> legacy degradation visibility (P1#5).

When the v2 pipeline fails to produce text (or some phases degrade to
partial), the final report MUST carry a visible degradation marker instead
of silently substituting a legacy/empty analysis.
"""

import pytest

from aimoon.adapters.driven.ai import prompt_builder as prompt_builder_mod
from aimoon.adapters.driven.ai.analyzer import DeepSeekAIAnalyzer
from aimoon.adapters.driven.ai.pipeline.orchestrator import PipelineOrchestrator
from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
from aimoon.core.domain.value_objects.analysis_report import AnalysisReport


class _FakeSettings:
    deepseek_model = "deepseek-chat"
    deepseek_temperature = 0.3
    deepseek_max_tokens = 1024
    mock_mode = False
    deepseek_api_key = ""
    deepseek_base_url = "http://fake"


def _canned_report() -> AnalysisReport:
    return AnalysisReport(
        symbol="600519",
        name="测试股",
        summary="s",
        report_text="legacy/partial text",
        investment_advice="x",
    )


async def _fake_legacy(*a, **k) -> AnalysisReport:
    return _canned_report()


@pytest.fixture
def _analyzer(monkeypatch):
    import aimoon.adapters.driven.ai.cache as ai_cache

    monkeypatch.setattr(prompt_builder_mod, "detect_industry", lambda s, n: "测试")
    monkeypatch.setattr(ai_cache, "set_analysis_cache", lambda *a, **k: None)
    a = DeepSeekAIAnalyzer(settings=_FakeSettings(), api_key="k", api_url="http://x")
    return a


@pytest.mark.asyncio
async def test_v2_empty_text_marks_degraded(monkeypatch, _analyzer):
    """v2 未产出文本 -> 降级 legacy,且报告带可见降级标记。"""

    async def _fake_run(self, *a, **k):
        return {}

    monkeypatch.setattr(PipelineOrchestrator, "run", _fake_run)
    monkeypatch.setattr(_analyzer, "_legacy_analyze", _fake_legacy)

    report = await _analyzer.analyze(
        StockAnalysis(symbol="600519"), use_pipeline_v2=True
    )
    assert "降级" in report.report_text
    assert "legacy 一段式" in report.report_text


@pytest.mark.asyncio
async def test_v2_partial_phases_marks_degraded(monkeypatch, _analyzer):
    """v2 部分阶段(partial) -> 报告带 partial 降级标记。"""

    async def _fake_run(self, *a, **k):
        return {"final_markdown": "v2 draft", "partial_phases": ["analysis"]}

    monkeypatch.setattr(PipelineOrchestrator, "run", _fake_run)

    report = await _analyzer.analyze(
        StockAnalysis(symbol="600519"), use_pipeline_v2=True
    )
    assert "v2 draft" in report.report_text
    assert "部分阶段降级" in report.report_text
    assert "analysis" in report.report_text
