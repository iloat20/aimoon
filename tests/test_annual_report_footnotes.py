"""Tests for 年报 PDF 附注解析(annual_report_pdf)— 消 8.1 缺失清单(C 组附注级)。

覆盖: 纯函数解析 parse_footnotes_from_text、渲染器 render_annual_report_footnotes、
端到端编排 fetch_annual_report_footnotes(网络桩)。真实年报文本 fixture 若存在则额外校验。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aimoon.adapters.driven.ai.pipeline.table_renderer import (
    render_annual_report_footnotes,
    render_quarterly_breakdown,
    render_region_breakdown,
)
from aimoon.adapters.driven.financial import annual_report_pdf as arp
from aimoon.core.domain.entities.financial import FinancialData

_FIXTURE = Path(__file__).parent / "fixtures" / "000651_annual_2025.txt"

_SYNTHETIC = """
公司对应收账款采用预期信用损失模型。本年度公司对应收账款办理无追索权保理，
终止确认应收账款 12,345,678.90 元，相关风险报酬已转移。
应收账款账龄分析显示：1 年以内 98,765,432.10 元，1-2 年 2,000,000.00 元。
存货跌价准备本年度计提 3,456,789.00 元，主要因部分库存商品可变现净值低于成本。
公司与关联方发生关联交易，关联交易金额合计 5,555,555.00 元，定价公允。
应付账款账龄：1 年以内 77,888,999.00 元，1 年以上 1,111,111.00 元。
"""


def test_parse_footnotes_finds_all_topics() -> None:
    res = arp.parse_footnotes_from_text(_SYNTHETIC, report_title="测试年报")
    assert res["available"] is True
    assert res["report_title"] == "测试年报"
    topics = {e["topic"] for e in res["excerpts"]}
    assert "应收账款保理与终止确认" in topics
    assert "应收账款账龄" in topics
    assert "存货跌价准备" in topics
    assert "关联交易" in topics
    assert "应付账款账龄与结构" in topics
    # 摘录需为清洗后的字符串且非空
    for e in res["excerpts"]:
        assert isinstance(e["text"], str) and e["text"].strip()


def test_parse_footnotes_empty_text() -> None:
    res = arp.parse_footnotes_from_text("")
    assert res["available"] is False
    assert res["excerpts"] == []


def test_render_footnotes_empty() -> None:
    fin = FinancialData(symbol="000651", annual_report_footnotes={})
    assert render_annual_report_footnotes(fin) == ""
    # available=False 也应返回空
    fin2 = FinancialData(
        symbol="000651",
        annual_report_footnotes={"available": False, "excerpts": []},
    )
    assert render_annual_report_footnotes(fin2) == ""


def test_render_footnotes_table() -> None:
    fn = {
        "report_title": "珠海格力电器股份有限公司 2025 年年度报告",
        "source": "巨潮资讯 PDF(年报)",
        "available": True,
        "excerpts": [
            {"topic": "应收账款保理与终止确认", "text": "本年度办理无追索权保理..."},
            {"topic": "存货跌价准备", "text": "存货跌价准备计提 345.68 万元..."},
        ],
    }
    fin = FinancialData(symbol="000651", annual_report_footnotes=fn)
    md = render_annual_report_footnotes(fin)
    assert "## 年报附注摘录" in md
    assert "应收账款保理与终止确认" in md
    assert "存货跌价准备" in md
    assert "2025 年年度报告" in md


def test_fetch_orchestration_mocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """端到端编排:桩掉网络(_cninfo_query/_download_pdf)与 PDF 解析(_extract_text),
    验证 fetch 返回与 parse 一致,且单源失败返回空结构。"""

    def fake_query(symbol: str, stock_name: str) -> list[dict]:
        return [{"title": "X 2025 年年度报告", "pdf_url": "http://x/y.PDF", "time": ""}]

    async def fake_download(url: str) -> bytes:
        return b"%PDF-1.4 fake"

    def fake_extract(data: bytes) -> str:
        return _SYNTHETIC

    monkeypatch.setattr(arp, "_cninfo_query", fake_query)
    monkeypatch.setattr(arp, "_download_pdf", fake_download)
    monkeypatch.setattr(arp, "_extract_text", fake_extract)

    res = asyncio.run(arp.fetch_annual_report_footnotes("000651", "格力电器"))
    assert res["available"] is True
    assert len(res["excerpts"]) == 5


def test_fetch_orchestration_network_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom_query(symbol: str, stock_name: str, client) -> list[dict]:
        raise RuntimeError("cninfo down")

    monkeypatch.setattr(arp, "_cninfo_query", boom_query)
    res = asyncio.run(arp.fetch_annual_report_footnotes("000651", "格力电器"))
    assert res["available"] is False
    assert res["excerpts"] == []


@pytest.mark.skipif(not _FIXTURE.exists(), reason="real annual-report text fixture missing")
def test_parse_footnotes_real_fixture() -> None:
    text = _FIXTURE.read_text(encoding="utf-8")
    res = arp.parse_footnotes_from_text(text, report_title="格力电器 2025 年年度报告")
    assert res["available"] is True
    # 真实年报至少应命中应收账款与关联交易两类附注
    topics = {e["topic"] for e in res["excerpts"]}
    assert "关联交易" in topics
    assert "应收账款账龄" in topics


# ===== 分季度主要财务指标 / 分地区(内销/外销) 解析与渲染测试(消 8.1 缺失清单 #1/#2) =====
_QUARTERLY_SYNTH = """
八、分季度主要财务指标
单位：元
项目 第一季度 第二季度 第三季度 第四季度
营业收入 41,506,860,074.79 55,818,065,913.41 39,855,167,155.65 33,266,965,389.72
归属于上市公司股东的净利润 5,904,459,443.46 8,507,947,670.38 7,048,929,692.70 7,541,766,605.12
经营活动产生的现金流量净额 11,001,218,583.01 17,327,343,604.19 17,399,821,498.00 654,731,068.82
"""

_REGION_SYNTH = """
（1）营业收入构成
分地区
内销-主营业务 126,407,077,425.42 74.16% 141,512,822,056.59 74.81% -10.67%
外销-主营业务 27,374,894,918.04 16.06% 28,202,530,945.88 14.91% -2.93%
分地区
内销-主营业务 126,407,077,425.42 82,766,067,955.42 34.52% -10.67% -11.05% 0.27
外销-主营业务 27,374,894,918.04 20,657,558,939.24 24.54% -2.93% -3.61% 0.53
"""


def test_parse_quarterly_breakdown_synthetic() -> None:
    res = arp.parse_quarterly_breakdown_from_text(_QUARTERLY_SYNTH)
    assert res["available"] is True
    qs = res["quarters"]
    assert len(qs) == 4
    assert qs[0]["quarter"] == "第一季度"
    assert qs[0]["revenue_yi"] == pytest.approx(415.07, abs=0.1)
    assert qs[0]["net_profit_yi"] == pytest.approx(59.04, abs=0.1)
    assert qs[3]["revenue_yi"] == pytest.approx(332.67, abs=0.1)


def test_parse_quarterly_breakdown_empty() -> None:
    assert arp.parse_quarterly_breakdown_from_text("")["available"] is False
    assert arp.parse_quarterly_breakdown_from_text("无相关章节")["available"] is False


def test_parse_region_breakdown_synthetic() -> None:
    res = arp.parse_region_breakdown_from_text(_REGION_SYNTH)
    assert res["available"] is True
    regs = {r["name"]: r for r in res["regions"]}
    assert "内销" in regs and "外销" in regs
    # 内销: 营收1264.07亿, 占比74.16%, 同比-10.67%, 毛利率34.52%
    n = regs["内销"]
    assert n["revenue_yi"] == pytest.approx(1264.07, abs=0.1)
    assert n["ratio"] == pytest.approx(74.16, abs=0.01)
    assert n["yoy"] == pytest.approx(-10.67, abs=0.01)
    assert n["gross_margin"] == pytest.approx(34.52, abs=0.01)
    w = regs["外销"]
    assert w["revenue_yi"] == pytest.approx(273.75, abs=0.1)
    assert w["gross_margin"] == pytest.approx(24.54, abs=0.01)


def test_parse_region_breakdown_empty() -> None:
    assert arp.parse_region_breakdown_from_text("")["available"] is False


def test_compute_quarterly_yoy() -> None:
    cur = {
        "available": True,
        "quarters": [{"quarter": "第一季度", "revenue_yi": 400.0, "net_profit_yi": 50.0}],
    }
    prev = {
        "available": True,
        "quarters": [{"quarter": "第一季度", "revenue_yi": 500.0, "net_profit_yi": 60.0}],
    }
    out = arp._compute_quarterly_yoy(cur, prev)
    q = out["quarters"][0]
    assert q["revenue_yoy"] == pytest.approx(-20.0, abs=0.1)
    assert q["net_profit_yoy"] == pytest.approx(-16.67, abs=0.1)


def test_render_quarterly_breakdown() -> None:
    fn = {
        "quarterly_breakdown": {
            "available": True,
            "quarters": [
                {"quarter": "第一季度", "revenue_yi": 415.07, "net_profit_yi": 59.04,
                 "revenue_yoy": -9.0, "net_profit_yoy": 5.0},
                {"quarter": "第二季度", "revenue_yi": 558.18, "net_profit_yi": 85.08},
            ],
        }
    }
    fin = FinancialData(symbol="000651", annual_report_footnotes=fn)
    md = render_quarterly_breakdown(fin)
    assert "## 分季度主要财务指标" in md
    assert "第一季度" in md and "第二季度" in md
    assert "415.1" in md


def test_render_region_breakdown() -> None:
    fn = {
        "region_breakdown": {
            "available": True,
            "regions": [
                {"name": "内销", "revenue_yi": 1264.07, "ratio": 74.16, "yoy": -10.67, "gross_margin": 34.52},
                {"name": "外销", "revenue_yi": 273.75, "ratio": 16.06, "yoy": -2.93, "gross_margin": 24.54},
            ],
        }
    }
    fin = FinancialData(symbol="000651", annual_report_footnotes=fn)
    md = render_region_breakdown(fin)
    assert "## 主营业务分地区" in md
    assert "内销" in md and "外销" in md
    assert "1264.1" in md


def test_render_quarterly_region_empty() -> None:
    fin = FinancialData(symbol="000651", annual_report_footnotes={})
    assert render_quarterly_breakdown(fin) == ""
    assert render_region_breakdown(fin) == ""


@pytest.mark.skipif(not _FIXTURE.exists(), reason="real annual-report text fixture missing")
def test_parse_quarterly_real_fixture() -> None:
    text = _FIXTURE.read_text(encoding="utf-8")
    res = arp.parse_quarterly_breakdown_from_text(text)
    assert res["available"] is True
    assert len(res["quarters"]) == 4
    # 真实格力 2025: Q4 营收约 332.67 亿
    assert res["quarters"][3]["revenue_yi"] == pytest.approx(332.67, abs=0.5)


@pytest.mark.skipif(not _FIXTURE.exists(), reason="real annual-report text fixture missing")
def test_parse_region_real_fixture() -> None:
    text = _FIXTURE.read_text(encoding="utf-8")
    res = arp.parse_region_breakdown_from_text(text)
    assert res["available"] is True
    regs = {r["name"]: r for r in res["regions"]}
    assert "内销" in regs
    assert regs["内销"]["revenue_yi"] == pytest.approx(1264.07, abs=0.5)
