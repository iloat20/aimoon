"""Tests for the peer_compare tool (Task 7)."""
from __future__ import annotations

import asyncio

import pytest

from aimoon.adapters.driven.ai.tools.peer_compare import (
    build_search_query,
    parse,
    run,
)
from aimoon.core.domain.entities.financial import FinancialData

MOCK_BING = (
    '<li class="b_algo">\n'
    '  <a href="http://example.com/midea">'
    '<b>美的集团</b> 000333 PE 12.30 ROE 25.1% 近三年净利润CAGR 9.1%'
    "</a>\n"
    "</li>\n"
    '<li class="b_algo">\n'
    '  <a href="http://example.com/haier">'
    '<b>海尔智家</b> 600690 PE 15.60 ROE 18.3% 近三年净利润CAGR 7.4%'
    "</a>\n"
    "</li>\n"
    '<li class="b_algo">\n'
    '  <a href="http://example.com/gree">'
    '<b>格力电器</b> 000651 PE 7.90 ROE 26.2% 近三年净利润CAGR 6.2%'
    "</a>\n"
    "</li>\n"
    '<li class="b_algo">\n'
    '  <a href="http://example.com/hisense">'
    '<b>海信视像</b> 600060 PE 14.00 ROE 12.5% 近三年净利润CAGR 11.0%'
    "</a>\n"
    "</li>\n"
)


def _self_fin() -> FinancialData:
    return FinancialData(symbol="600519", report_period="2024-12-31")


# run() 是 async 的(search_fn 为 async execute_web_search),同步测试用 asyncio.run 包装。
def _run(*args, **kwargs) -> dict:
    return asyncio.run(run(*args, **kwargs))


def test_build_search_query_contains_name_and_keywords() -> None:
    q = build_search_query("贵州茅台", industry="白酒")
    assert "贵州茅台" in q
    assert "PE" in q or "同行" in q or "竞品" in q


def test_build_search_query_without_industry() -> None:
    q = build_search_query("贵州茅台")
    assert "贵州茅台" in q
    assert "PE" in q or "同行" in q or "竞品" in q


def test_parse_extracts_at_least_three_peers_from_html() -> None:
    peers = parse(MOCK_BING, _self_fin())
    assert len(peers) >= 3
    names = [p["name"] for p in peers]
    assert "美的集团" in names
    assert "格力电器" in names
    # 解析字段存在且为数值
    for p in peers[:3]:
        assert "name" in p
        assert isinstance(p.get("pe", 0.0), float)
        assert isinstance(p.get("pb", 0.0), float)
        assert isinstance(p.get("roe", 0.0), float)
        assert isinstance(p.get("np_cagr", 0.0), float)


def test_parse_extracts_numeric_values_correctly() -> None:
    peers = parse(MOCK_BING, _self_fin())
    midea = next(p for p in peers if p["name"] == "美的集团")
    assert midea["pe"] == pytest.approx(12.3, abs=0.01)
    assert midea["roe"] == pytest.approx(25.1, abs=0.01)
    assert midea["np_cagr"] == pytest.approx(9.1, abs=0.01)


def test_parse_empty_html_returns_empty_list() -> None:
    assert parse("", _self_fin()) == []


def test_run_with_mock_search_returns_peers_when_search_provided() -> None:
    async def fake_search(query: str) -> str:
        return MOCK_BING

    out = _run("美的集团", _self_fin(), search_fn=fake_search)
    assert "__partial__" not in out
    assert len(out["peers"]) >= 3
    assert out["industry"] == "白色家电"


def test_run_without_search_returns_partial() -> None:
    out = _run("美的集团", _self_fin())
    assert out["__partial__"] == "no_data"


def test_run_with_empty_name_returns_partial() -> None:
    out = _run("", _self_fin())
    assert "__partial__" in out


@pytest.mark.unit
def test_run_no_html_returns_peers_shape() -> None:
    # search_fn 返回空 html → 仍然返回 {peers, industry} 标准形状
    async def _empty_search(_q: str) -> str:
        return ""

    out = _run(name="贵州茅台", self_fin=None, search_fn=_empty_search)
    assert isinstance(out.get("peers"), list)
    assert "industry" in out
