"""Tests for 渠道代理指标(合同负债)采集与渲染 — 消 8.1 缺失清单 #3(代理)。

合同负债是经销商预收打款蓄水池,作为「经销商数量/渠道库存」的确定性代理指标。
fixture 使用真实东财/新浪列名,避免 mock 掩盖真实解析 bug。
"""
from __future__ import annotations

import pandas as pd
import pytest

from aimoon.adapters.driven.ai.pipeline.table_renderer import (
    render_channel_proxy,
)
from aimoon.adapters.driven.financial.akshare_adapter import (
    AkshareFinancialAdapter,
)
from aimoon.core.domain.entities.financial import FinancialData


def _em_bs_df() -> pd.DataFrame:
    """真实东财资产负债表列名 + 合同负债(新准则)。"""
    return pd.DataFrame(
        {
            "REPORT_DATE": ["2025-12-31"],
            "REPORT_TYPE": ["年报"],
            "TOTAL_ASSETS": [3.68e11],
            "CONTRACT_LIABILITIES": [1.5e11],  # 1500 亿
        }
    )


def _sina_bs_df() -> pd.DataFrame:
    """真实新浪资产负债表列名 + 合同负债(中文)。"""
    return pd.DataFrame(
        {
            "报告日": ["2025-12-31"],
            "资产总计": [3.68e11],
            "合同负债": [1.2e11],  # 1200 亿
        }
    )


def test_parse_contract_liabilities_em(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(AkshareFinancialAdapter, "__init__", lambda self: None)
    adapter = AkshareFinancialAdapter()
    fin = FinancialData(symbol="000651")
    adapter._parse_balance_sheet(fin, _em_bs_df())
    assert fin.contract_liabilities == 1.5e11


def test_parse_contract_liabilities_sina(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(AkshareFinancialAdapter, "__init__", lambda self: None)
    adapter = AkshareFinancialAdapter()
    fin = FinancialData(symbol="000651")
    adapter._parse_balance_sina(fin, _sina_bs_df())
    assert fin.contract_liabilities == 1.2e11


def test_parse_contract_liabilities_sina_only_fills_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """东财已解析出值时,新浪兜底不应覆盖(仅填空字段)。"""
    monkeypatch.setattr(AkshareFinancialAdapter, "__init__", lambda self: None)
    adapter = AkshareFinancialAdapter()
    fin = FinancialData(symbol="000651", contract_liabilities=1.5e11)
    adapter._parse_balance_sina(fin, _sina_bs_df())
    assert fin.contract_liabilities == 1.5e11  # 未被 1.2e11 覆盖


def test_render_channel_proxy_empty() -> None:
    fin = FinancialData(symbol="000651")
    assert render_channel_proxy(fin) == ""


def test_render_channel_proxy_table_with_yoy() -> None:
    fin = FinancialData(
        symbol="000651",
        contract_liabilities=1.5e11,  # 1500 亿
        contract_liabilities_prev=1.2e11,  # 1200 亿
        revenue=2.0e11,  # 2000 亿
    )
    md = render_channel_proxy(fin)
    assert "## 渠道代理指标" in md
    assert "1500.0" in md  # _fmt_num(1.5e11) -> 1500.0 亿
    assert "1200.0" in md  # 上一年
    assert "+25.0%" in md  # (1500-1200)/1200 = 25%
    assert "75.0%" in md  # 1500/2000 = 75%
    assert "压货" in md  # cl>prev -> 压货/备货积极
    assert "代理" in md  # 明确标注代理值
    assert "见渠道代理指标表" not in md  # 表本身不内联引用自己的 token


def test_render_channel_proxy_no_prev_shows_na() -> None:
    fin = FinancialData(
        symbol="000651",
        contract_liabilities=1.5e11,
        revenue=2.0e11,
    )
    md = render_channel_proxy(fin)
    assert "1500.0" in md
    assert "N/A" in md  # 同比 / 上一年 缺省
    assert "仅看绝对蓄水池" in md
