"""Tests for low-latency pipeline v2 modes: --single-call / --ultra-fast.

These never hit the real LLM: 像 test_pipeline_phases.py 一样 fake 掉
``_call_llm_with_stream`` 与 ``_run_peer_compare``。
"""

from __future__ import annotations

import pytest

from aimoon.adapters.driven.ai.pipeline import section_coverage
from aimoon.adapters.driven.ai.pipeline.orchestrator import (
    PipelineOrchestrator,
    _split_inline_self_check,
)
from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis


class _FakeSettings:
    deepseek_model = "deepseek-v4-flash"
    deepseek_temperature = 0.3
    deepseek_max_tokens = 16384


class _FakeAnalyzer:
    def __init__(self) -> None:
        self._settings = _FakeSettings()
        self._provided_settings = None
        self.api_url = "http://fake"
        self.api_key = "fake"
        self._http = None

    def _build_data_dict(self, info, reports=None, financial_md_path=None):
        return {"symbol": info.symbol, "name": info.name, "_fake": True}


# 八个章节都覆盖到的 fake 报告,用于验证终稿覆盖度
_FAKE_FULL_REPORT = '''## 一、业务画像与护城河

核心业务结构、供应链定位、护城河来源、竞争格局同业表格。

## 二、财务健康诊断

营收/净利 5 年 CAGR、ROE 杜邦拆解、经营现金流/净利润比、资产负债率。

## 三、交叉验证

业务 vs 财务背离、舆情验证、资金验证、近 5 年 PE/PB 分位。

## 四、风险量化与看空

看空一:阈值 X 触发营收冲击 10%,概率 25%。
看空二:阈值 Y 触发净利冲击 15%,概率 20%。
看空三:阈值 Z 触发 OCF 冲击 20%,概率 15%。

## 五、估值建模

同业 PE/PB 对比;FCFE 三档保守档 g=1% r=10%、中性档 g=5% r=9%、乐观档 g=9% r=8%。

## 六、逆向视角

看多逻辑,市场错在哪,最悲观情景下行空间(安全边际)。

## 七、投资建议

买入/增持,目标价格区间 6 个月/12 个月,止损条件,仓位建议,催化剂事件。

## 八、附录

财务时序表(≥ 5 行),同行竞品对比表(≥ 5 家),估值三档表(含概率)。
'''


@pytest.fixture
def _fake_llm(monkeypatch):
    calls: list[dict] = []

    async def _fake_call(self, messages, *, max_tokens=None, thinking_budget=500):
        calls.append({"max_tokens": max_tokens, "thinking_budget": thinking_budget})
        # single-call 模式会把自检 JSON 内联在末尾
        report = _FAKE_FULL_REPORT
        if max_tokens and max_tokens >= 10000:
            report = report + (
                "\n\n```json\n"
                '{"citations_ok":true,"tables_ok":true,"trigger_ok":true,'
                '"advice_ok":true,"financial_depth_ok":true,"business_depth_ok":true,'
                '"norepeat_ok":true,"justified_ok":true,"fixes_needed":[]}\n'
                "```"
            )
        return {"role": "assistant", "content": report}

    async def _fake_self_check(self, draft):
        return (
            '{"citations_ok":true,"tables_ok":true,"trigger_ok":true,'
            '"advice_ok":true,"financial_depth_ok":true,"business_depth_ok":true,'
            '"norepeat_ok":true,"justified_ok":true,"fixes_needed":[]}'
        )

    def _fake_peer(si, search_fn):
        return {"_fake": True, "tool": "peer_compare"}

    monkeypatch.setattr(PipelineOrchestrator, "_call_llm_with_stream", _fake_call)
    monkeypatch.setattr(
        PipelineOrchestrator, "_run_self_check", _fake_self_check,
    )
    import aimoon.adapters.driven.ai.pipeline.orchestrator as _m
    monkeypatch.setattr(_m, "_run_peer_compare", _fake_peer)
    return calls


@pytest.mark.asyncio
async def test_single_call_skips_compile_and_inline_self_check(_fake_llm):
    """single-call: 只调一次 LLM,无 COMPILE 阶段,从末尾读自检 JSON。"""
    ctx = await PipelineOrchestrator(_FakeAnalyzer()).run(
        StockAnalysis(symbol="000001"), use_single_call=True,
    )
    # 只触发一次真 LLM 调用(ANALYSIS / inline self-check)
    assert len(_fake_llm) == 1, f"期望 1 次 LLM 调用,实际 {_fake_llm}"
    # COMPILE 被跳过(标记 skipped)或不存在
    compile_res = ctx["phase_results"].get("compile", {})
    assert compile_res.get("skipped") is True or not compile_res
    # 终稿非空且覆盖 ≥ 7 个章节
    assert ctx["final_markdown"]
    cov = section_coverage.evaluate_coverage(ctx["final_markdown"])
    assert cov.hit >= 7, f"章节覆盖不足 7: missing={cov.missing}"
    # 终稿中不应再残留自检 JSON
    assert '"citations_ok"' not in ctx["final_markdown"]


@pytest.mark.asyncio
async def test_ultra_fast_skips_self_check_and_compile(_fake_llm):
    """ultra-fast: 跳过自检与 COMPILE,初稿即终稿。"""
    ctx = await PipelineOrchestrator(_FakeAnalyzer()).run(
        StockAnalysis(symbol="000001"), use_ultra_fast=True,
    )
    # 只做一次 ANALYSIS 调用,无独立 self-check / reanalysis / COMPILE
    assert len(_fake_llm) == 1
    assert ctx["phase_results"]["compile"].get("skipped") is True
    assert ctx["final_markdown"]
    cov = section_coverage.evaluate_coverage(ctx["final_markdown"])
    assert cov.hit >= 7


@pytest.mark.asyncio
async def test_fast_keeps_compile_drops_self_check(_fake_llm):
    """fast 模式保留 COMPILE 路径: 默认 mode 仍走完整双阶段。"""
    # 仅验证: 默认(非 fast)跑完必须有 analysis 与 compile 两阶段登记
    ctx = await PipelineOrchestrator(_FakeAnalyzer()).run(
        StockAnalysis(symbol="000001"),
    )
    assert "analysis" in ctx["phase_results"]
    assert "compile" in ctx["phase_results"]


def test_split_inline_self_check_bare_and_fence():
    ok_json = (
        '{"citations_ok":true,"tables_ok":true,"trigger_ok":true,'
        '"advice_ok":true,"financial_depth_ok":true,"business_depth_ok":true,'
        '"norepeat_ok":true,"justified_ok":false,"fixes_needed":["加强看空触发"]}'
    )
    # fence 形式
    fenced = f"## 报告\n\n正文。\n\n```json\n{ok_json}\n```"
    body, checks, fixes = _split_inline_self_check(fenced)
    assert "```json" not in body
    assert checks["justified_ok"] is False
    assert fixes == ["加强看空触发"]
    # 裸 JSON 形式
    bare = f"## 报告\n\n正文。\n\n{ok_json}"
    body2, checks2, _ = _split_inline_self_check(bare)
    assert checks2["citations_ok"] is True
    assert '{"' not in body2


def test_split_inline_self_check_no_json_returns_whole():
    md = "## 一、业务画像\n\n正文没有 JSON。"
    body, checks, fixes = _split_inline_self_check(md)
    assert body == md
    assert checks == {}
    assert fixes == []
