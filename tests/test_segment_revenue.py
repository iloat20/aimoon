"""Tests for 分业务营收(segment revenue)采集与渲染 — 消 8.1 缺失清单 #3。

fixture 使用真实东财 F10 列名(主营构成/主营收入/分类类型/收入比例/
毛利率/报告日期),而非臆想列名,避免 mock 掩盖真实解析 bug。
"""
from __future__ import annotations

import asyncio

import pandas as pd
import pytest

from aimoon.adapters.driven.ai.pipeline.table_renderer import (
    render_segment_revenue,
)
from aimoon.adapters.driven.financial.akshare_adapter import (
    AkshareFinancialAdapter,
)
from aimoon.core.domain.entities.financial import FinancialData


def _fake_df() -> pd.DataFrame:
    """真实东财 F10 列名 + 混入旧期/非产品分类,验证过滤逻辑。

    - 2025-12-31 按产品分类:消费电器、工业制品及绿色能源(应为结果)
    - 2025-12-31 按行业分类:制造业(应被 seg_col 过滤掉)
    - 2025-06-30 按产品分类:智能装备(应被最新期过滤掉)
    """
    return pd.DataFrame(
        {
            "股票代码": ["000651"] * 4,
            "报告日期": [
                "2025-12-31",
                "2025-12-31",
                "2025-12-31",
                "2025-06-30",
            ],
            "分类类型": [
                "按产品分类",
                "按产品分类",
                "按行业分类",
                "按产品分类",
            ],
            "主营构成": [
                "消费电器",
                "工业制品及绿色能源",
                "制造业",
                "智能装备",
            ],
            "主营收入": [1330.55e8, 173.81e8, 1537.82e8, 6.81e8],
            "收入比例": [0.7806, 0.1020, 0.9022, 0.0040],
            "毛利率": [0.3528, 0.1553, 0.3275, 0.2063],
        }
    )


def test_fetch_segment_parses_latest_product_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    def fake(symbol: str) -> pd.DataFrame:
        captured["symbol"] = symbol
        return _fake_df()

    monkeypatch.setattr(AkshareFinancialAdapter, "__init__", lambda self: None)
    adapter = AkshareFinancialAdapter()
    monkeypatch.setattr(
        "aimoon.adapters.driven.financial.akshare_adapter.ak.stock_zygc_em",
        fake,
    )
    segs = asyncio.run(adapter._fetch_segment("SZ000651"))
    assert captured["symbol"] == "SZ000651"
    # 仅最新期(2025-12-31)的按产品分类:消费电器 + 工业制品及绿色能源
    assert len(segs) == 2
    names = [s["name"] for s in segs]
    assert "制造业" not in names  # 按行业分类被过滤
    assert "智能装备" not in names  # 旧期(2025-06-30)被过滤
    assert segs[0]["name"] == "消费电器"
    assert segs[0]["revenue_yi"] == 1330.55
    assert segs[0]["ratio"] == 0.7806
    assert segs[0]["gross_margin"] == 0.3528


def test_fetch_segment_empty_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(symbol: str) -> pd.DataFrame:
        raise RuntimeError("network")

    monkeypatch.setattr(AkshareFinancialAdapter, "__init__", lambda self: None)
    adapter = AkshareFinancialAdapter()
    monkeypatch.setattr(
        "aimoon.adapters.driven.financial.akshare_adapter.ak.stock_zygc_em",
        boom,
    )
    assert asyncio.run(adapter._fetch_segment("SZ000651")) == []


def test_render_segment_revenue_empty() -> None:
    fin = FinancialData(symbol="000651", segment_revenue=[])
    assert render_segment_revenue(fin) == ""


def test_render_segment_revenue_table() -> None:
    fin = FinancialData(
        symbol="000651",
        segment_revenue=[
            {"name": "消费电器", "revenue_yi": 1330.55, "ratio": 0.7806, "gross_margin": 0.3528},
            {
                "name": "工业制品及绿色能源",
                "revenue_yi": 173.81,
                "ratio": 0.1020,
                "gross_margin": 0.1553,
            },
        ],
    )
    md = render_segment_revenue(fin)
    assert "## 分业务营收" in md
    assert "消费电器" in md
    assert "1330.5" in md  # _fmt_num 四舍五入
    assert "78.1%" in md  # _pct(0.7806) -> 78.1%
    assert "35.3%" in md  # _pct(0.3528) -> 35.3%
