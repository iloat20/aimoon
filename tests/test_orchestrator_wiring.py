"""Regression tests for PipelineOrchestrator v2 tool wiring (P0 fixes).

These tests exercise the REAL ``_phase_analysis`` wiring with the actual
``_run_peer_compare`` / ``valuation`` runners, mocking only the network/LLM
boundaries.  They guard against two of the three blocking bugs found in audit:

* P0#2 — ``valuation`` called with swapped args (fin, peer, quote).
* P0#3 — ``peer_compare`` produced a bare list, breaking the peer table.

(P0#1 — tool-cache HIT NameError — was retired when the 60s tool cache layer
was removed in P3#15; tools now always recompute.)
"""

import pytest

import aimoon.adapters.driven.ai.cache as ai_cache
import aimoon.adapters.driven.ai.pipeline.orchestrator as orch_mod
import aimoon.adapters.driven.ai.web_search_tool as wst
from aimoon.adapters.driven.ai.pipeline.orchestrator import PipelineOrchestrator
from aimoon.adapters.driven.ai.pipeline.table_renderer import render_peer_comparison
from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis


class _FakeSettings:
    deepseek_model = "deepseek-chat"
    deepseek_max_tokens = 4096


class _FakeAnalyzer:
    """Minimal analyzer shim satisfying PipelineOrchestrator.AnalyzerRuntime."""

    _settings = _FakeSettings()
    _provided_settings = None
    api_url = "http://fake"
    api_key = "fake"
    _http = None

    def _build_data_dict(self, info, reports=None, financial_md_path=None):
        return {"quote": {"price": 100}, "industry": "白酒"}

    async def _stream_final_response(self, messages):
        return "[compiled fake markdown]"


_FAKE_BING_HTML = (
    '<li class="b_algo"><a>贵州茅台 PE 30 PB 10 ROE 20 净利润CAGR 15</a></li>'
)


@pytest.fixture
def _wired(monkeypatch):
    """Wire orchestrator with fake tool runners + mocked network/LLM edges."""
    calls: list[tuple[str, tuple]] = []

    def _recorder(name):
        def _fn(*args):
            calls.append((name, args))
            return {"_fake": name}

        return _fn

    fake_runners = {
        "technicals": _recorder("technicals"),
        "financial_temporal": _recorder("financial_temporal"),
        "peer_compare": _recorder("peer_compare"),
        "business_moat": _recorder("business_moat"),
        "risk_quant": _recorder("risk_quant"),
        "valuation": _recorder("valuation"),
        "sentiment": _recorder("sentiment"),
        "fcf_dividend": _recorder("fcf_dividend"),
        "scenario_prob": _recorder("scenario_prob"),
    }
    monkeypatch.setattr(orch_mod, "TOOL_RUNNERS", fake_runners)

    async def _fake_llm(self, messages, *, max_tokens=None, reasoning_effort="max"):
        return {"role": "assistant", "content": "## 一、概况\n\n(fake draft)"}

    monkeypatch.setattr(PipelineOrchestrator, "_call_llm_with_stream", _fake_llm)

    async def _fake_search(q, max_results=5):
        return _FAKE_BING_HTML

    monkeypatch.setattr(wst, "execute_web_search", _fake_search)
    monkeypatch.setattr(ai_cache, "get_cached_report", lambda *a, **k: "")
    monkeypatch.setattr(ai_cache, "set_cached_report", lambda *a, **k: None)

    return calls


async def _run_phase(monkeypatch):
    orch = PipelineOrchestrator(_FakeAnalyzer())
    return await orch._phase_analysis(
        StockAnalysis(symbol="600519", name="贵州茅台"),
        stock_md="# 标的快照",
        prior={},
        reports=None,
        financial_md_path=None,
        use_fast=False,
    )


@pytest.mark.asyncio
async def test_peer_compare_result_is_dict_with_peers(monkeypatch, _wired):
    """P0#3: _run_peer_compare must yield {'peers': [...]} not a bare list."""
    result = await _run_phase(monkeypatch)
    peer = result["tool_results"]["peer_compare"]
    assert isinstance(peer, dict), f"peer_compare 应为 dict,实际 {type(peer)}"
    assert "peers" in peer, "peer_compare 缺少 'peers' 键"
    assert isinstance(peer["peers"], list) and peer["peers"], "peers 解析为空"
    # 渲染层能消费 -> 同行对比表出现
    rendered = render_peer_comparison(peer)
    assert "同行竞品对比表" in rendered, "同行对比表未渲染"


@pytest.mark.asyncio
async def test_valuation_called_with_correct_arg_order(monkeypatch, _wired):
    """P0#2: valuation(fin_temporal, quote, peer_comp) — 顺序不能错。"""
    captured = {}

    def _val(*args):
        captured["args"] = args
        return {"pe": 1, "pb": 1, "fcfe_targets": {}, "fcfe_assumptions": {}}

    import aimoon.adapters.driven.ai.tools as tools_mod

    monkeypatch.setattr(orch_mod, "TOOL_RUNNERS", {**tools_mod.TOOL_RUNNERS, "valuation": _val})
    await _run_phase(monkeypatch)

    args = captured.get("args")
    assert args is not None, "valuation 未被调用"
    fin_temporal, quote, peer_comp = args
    # 第 2 个参数必须是 quote(StockQuote,带 pe),第 3 个必须是 peer dict(带 peers)
    assert hasattr(quote, "pe"), "第 2 参数应为 quote(StockQuote)"
    assert isinstance(peer_comp, dict) and "peers" in peer_comp, (
        "第 3 参数应为 peer_compare dict"
    )


@pytest.mark.asyncio
async def test_analysis_retries_on_http_status_error(monkeypatch, _wired):
    """BUG-3: 瞬时 LLM HTTP 429/5xx 必须重试(HTTPStatusError 现已在重试元组内)。

    修复前 HTTPStatusError 不在重试元组 (TransportError, OSError, TimeoutError)
    中,限流响应会直接透传导致静默降级为空分析。现重试至多 3 次,全部失败才降级。
    """
    import httpx

    state = {"calls": 0}

    async def _flaky_llm(self, messages, *, max_tokens=None, reasoning_effort="max"):
        state["calls"] += 1
        if state["calls"] < 3:
            raise httpx.HTTPStatusError(
                "rate limited",
                request=httpx.Request("POST", "http://fake"),
                response=httpx.Response(429),
            )
        return {"role": "assistant", "content": "## 一、概况\n\n(重试成功草稿)"}

    # 覆盖 fixture 中的固定 _fake_llm,注入「前 2 次失败、第 3 次成功」行为
    monkeypatch.setattr(PipelineOrchestrator, "_call_llm_with_stream", _flaky_llm)

    result = await _run_phase(monkeypatch)
    assert state["calls"] >= 3, "HTTP 错误后未重试到成功"
    assert result.get("empty_analysis") is not True, "HTTP 错误后重试应成功,不应标记空分析"
    assert result["output"], "重试成功后 ANALYSIS 草稿应为非空"
