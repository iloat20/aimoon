"""Tests for the peer_compare tool (Task 7)."""
from __future__ import annotations

import asyncio

import pytest

from aimoon.adapters.driven.ai.pipeline.table_renderer import render_peer_comparison
from aimoon.adapters.driven.ai.tools.peer_compare import (
    _validate_peers,
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


def test_run_without_search_uncurated_returns_partial() -> None:
    # 未策展行业 + 无 search_fn → 仍走 partial 降级(纯单测,不触网)。
    out = _run("某冷门设备股份", _self_fin())
    assert out["__partial__"] == "no_data"


def test_run_curated_industry_without_search_returns_peers(monkeypatch) -> None:
    # 策展行业(白色家电)即便无 search_fn 也能经 _curated_peers 出真实 peer。
    # mock 掉网络抓取,验证 run 直接采用策展结果、不回退 partial。
    fake = [
        {"name": "美的集团", "price": 81.0, "pe": 14.0, "pb": 3.0,
         "roe": 0.0, "np_cagr": 0.0, "rev_g": 0.0, "np_g": 0.0,
         "mcap": 6200.0, "self": False},
        {"name": "海尔智家", "price": 20.0, "pe": 10.0, "pb": 1.5,
         "roe": 0.0, "np_cagr": 0.0, "rev_g": 0.0, "np_g": 0.0,
         "mcap": 1900.0, "self": False},
    ]
    import aimoon.adapters.driven.ai.tools.peer_compare as pc_mod

    async def _fake_curated(self_fin, industry):
        return list(fake)

    monkeypatch.setattr(pc_mod, "_curated_peers", _fake_curated)
    out = _run("格力电器", _self_fin())
    assert "__partial__" not in out
    assert len(out["peers"]) == 2
    assert out["industry"] == "白色家电"
    assert out["peers"][0]["name"] == "美的集团"


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


# ---------- 数据异常自检(_validate_peers) ----------

def _peer(name: str, pe: float) -> dict:
    return {"name": name, "price": 1.0, "pe": pe, "pb": 1.0, "roe": 0.0,
            "np_cagr": 0.0, "rev_g": 0.0, "np_g": 0.0, "mcap": 1.0, "self": False}


def test_validate_peers_polluted_all_equal_self() -> None:
    # 致命场景:所有同行 PE 与标的自身 PE(7.65)完全一致 → 判定异常并清空。
    peers = [_peer("美的集团", 7.65), _peer("海尔智家", 7.65), _peer("海信家电", 7.65)]
    cleaned, quality, msg = _validate_peers(peers, 7.65)
    assert quality == "anomaly"
    assert cleaned == []  # 污染项被剔除
    assert "污染" in msg and "失效" in msg


def test_validate_peers_polluted_two_close_to_self() -> None:
    # ≥2 家同行 PE 与标的高度一致(≤2%)即触发,即便非全部。
    peers = [_peer("美的集团", 7.7), _peer("海尔智家", 7.6), _peer("海信家电", 14.0)]
    cleaned, quality, _ = _validate_peers(peers, 7.65)
    assert quality == "anomaly"
    # 仅保留明显不同的海信
    assert [p["name"] for p in cleaned] == ["海信家电"]


def test_validate_peers_clean_not_flagged() -> None:
    # 正常分散的同行 PE 不应触发异常。
    peers = [_peer("美的集团", 14.25), _peer("海尔智家", 10.53), _peer("海信家电", 11.75)]
    cleaned, quality, _ = _validate_peers(peers, 7.65)
    assert quality == "ok"
    assert len(cleaned) == 3


def test_validate_peers_self_pe_none_or_zero_skips() -> None:
    # 无基准(self_pe=None/0)时跳过自检,原样返回,避免误伤。
    peers = [_peer("美的集团", 7.65), _peer("海尔智家", 7.65)]
    assert _validate_peers(peers, None)[1] == "ok"
    assert _validate_peers(peers, 0.0)[1] == "ok"


def test_render_peer_comparison_shows_anomaly_warning() -> None:
    data = {
        "peers": [_peer("美的集团", 14.25)],
        "industry": "白色家电",
        "data_quality": "anomaly",
        "anomaly_msg": "同行 PE 与标的高度一致",
    }
    out = render_peer_comparison(data)
    assert "⚠️" in out
    assert "同行数据异常" in out


def test_render_peer_comparison_roe_blank_not_zero() -> None:
    # 同行 ROE/营收增速/净利增速 未采集(=0 哨兵)时整列隐藏:
    # 既不渲染误导性的 0.0%,也不留一排空列(P1 #11 完整性修正)。
    data = {"peers": [_peer("美的集团", 14.25)], "industry": "白色家电"}
    out = render_peer_comparison(data)
    assert "ROE(%)" not in out
    assert "营收增速(%)" not in out
    assert "净利增速(%)" not in out
    assert "0.0%" not in out
    assert "空列已隐藏" in out
