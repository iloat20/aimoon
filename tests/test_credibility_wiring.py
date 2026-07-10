"""回归测试：orchestrator 产出的 credibility 经 analyzer 透传到 AnalysisReport。

Task 5 把质量护栏摘要写进 ctx.to_dict()["credibility"]；这里验证该值
不被 analyzer 丢弃，最终进入 AnalysisReport.credibility（service 调
report_generator.generate 时再透传到 HTML 页脚）。
"""

import pytest

from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
from aimoon.core.domain.entities.quote import StockQuote
from aimoon.core.domain.value_objects.analysis_report import AnalysisReport


def test_analysis_report_has_credibility_default():
    """默认 credibility 为空 dict，保证 mock/legacy 路径不崩。"""
    assert AnalysisReport().credibility == {}


@pytest.mark.asyncio
async def test_pipeline_analyze_threads_credibility(monkeypatch):
    """orchestrator 返回的 credibility 经 analyzer 进入 AnalysisReport.credibility。"""
    from aimoon.adapters.driven.ai.analyzer import DeepSeekAIAnalyzer

    cred = {"checked": 3, "corrected": 1, "uncertain": []}

    async def fake_run(self, stock_info, **kw):
        return {
            "final_markdown": "# 报告",
            "credibility": cred,
            "partial_phases": [],
        }

    monkeypatch.setattr(
        "aimoon.adapters.driven.ai.pipeline.orchestrator.PipelineOrchestrator.run",
        fake_run,
    )

    analyzer = DeepSeekAIAnalyzer()
    stock = StockAnalysis(symbol="600519", name="测试", quote=StockQuote())
    report = await analyzer._pipeline_analyze(stock, use_single_call=True)

    assert report.credibility == cred
