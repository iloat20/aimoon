"""东财 WAF 期间, 新浪现金流量表/利润表/资产负债表兜底解析的单测(离线, 不联网)。

背景: 东财 stock_*_by_report_em 全接口被 WAF 拦截(返回 challenge 页, akshare 解析报
NoneType 下标错误)。适配器改走新浪 stock_financial_report_sina(走新浪、绕开 WAF),
_parse_*_sina 把中文列名行解析进 FinancialData。本文件用假 DataFrame 锁定解析逻辑,
避免后续回归把 capex/OCF/ICF 再解析错。
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from aimoon.adapters.driven.financial.akshare_adapter import (
    AkshareFinancialAdapter,
    _is_annual_report,
)
from aimoon.core.domain.entities.financial import FinancialData

YI = 1e8


class _NoCache:
    """禁用磁盘缓存, 保证 fetch 走真实(被 mock 的)逻辑而非命中陈旧缓存。

    fetch/quarterly/history 路径已改用异步缓存接口(aget/aset,内部 to_thread 卸载
    阻塞磁盘 IO);同步 get/set 保留以兼容任何遗留调用点。
    """

    def get(self, _k):
        return None

    def set(self, _k, _v):
        pass

    async def aget(self, _k):
        return None

    async def aset(self, _k, _v):
        pass


def _cf_df():
    return pd.DataFrame([
        {"报告日": "20251231",
         "购建固定资产、无形资产和其他长期资产所支付的现金": 17.17 * YI,
         "经营活动产生的现金流量净额": 463.83 * YI,
         "投资活动产生的现金流量净额": -485.99 * YI,
         "筹资活动产生的现金流量净额": 86.06 * YI,
         "分配股利、利润或偿付利息所支付的现金": 181.71 * YI},
        {"报告日": "20241231",
         "购建固定资产、无形资产和其他长期资产所支付的现金": 32.99 * YI,
         "经营活动产生的现金流量净额": 293.69 * YI,
         "投资活动产生的现金流量净额": -155.58 * YI,
         "筹资活动产生的现金流量净额": 55.0 * YI,
         "分配股利、利润或偿付利息所支付的现金": 170.0 * YI},
    ])


def _income_df():
    return pd.DataFrame([
        {"报告日": "20251231", "营业收入": 1704.47 * YI, "净利润": 288.63 * YI,
         "基本每股收益": 5.2},
        {"报告日": "20241231", "营业收入": 1700.0 * YI, "净利润": 300.0 * YI,
         "基本每股收益": 5.4},
    ])


def _balance_df():
    return pd.DataFrame([
        {"报告日": "20251231", "资产总计": 3913.72 * YI, "负债合计": 2415.81 * YI,
         "所有者权益(或股东权益)合计": 1497.91 * YI, "货币资金": 1105.53 * YI,
         "应收账款": 130.0 * YI, "存货": 281.83 * YI, "在建工程": 13.38 * YI},
        {"报告日": "20241231", "资产总计": 3800.0 * YI, "负债合计": 2300.0 * YI,
         "所有者权益(或股东权益)合计": 1500.0 * YI, "货币资金": 1000.0 * YI,
         "应收账款": 120.0 * YI, "存货": 270.0 * YI, "在建工程": 10.0 * YI},
    ])


def test_is_annual_report():
    assert _is_annual_report("20251231")
    assert _is_annual_report("2025-12-31")
    assert not _is_annual_report("20250930")
    assert not _is_annual_report("2025-09-30")


def test_parse_cash_flow_sina_fills_capex_and_flows():
    adp = AkshareFinancialAdapter()
    res = FinancialData(symbol="000651")
    adp._parse_cash_flow_sina(res, _cf_df())
    # 取最新年报(2025)
    assert res.capex == pytest.approx(17.17 * YI)
    assert res.operating_cf == pytest.approx(463.83 * YI)
    assert res.investing_cf == pytest.approx(-485.99 * YI)
    assert res.financing_cf == pytest.approx(86.06 * YI)
    assert res.dividend_paid == pytest.approx(181.71 * YI)


def test_parse_income_sina_fills_core_fields():
    adp = AkshareFinancialAdapter()
    res = FinancialData(symbol="000651")
    adp._parse_income_sina(res, _income_df())
    assert res.revenue == pytest.approx(1704.47 * YI)
    assert res.net_profit == pytest.approx(288.63 * YI)
    assert res.eps == pytest.approx(5.2)


def test_parse_balance_sina_fills_core_fields():
    adp = AkshareFinancialAdapter()
    res = FinancialData(symbol="000651")
    adp._parse_balance_sina(res, _balance_df())
    assert res.total_assets == pytest.approx(3913.72 * YI)
    assert res.total_liabilities == pytest.approx(2415.81 * YI)
    assert res.equity == pytest.approx(1497.91 * YI)
    assert res.monetary_funds == pytest.approx(1105.53 * YI)
    # 东财软封走新浪时, 应收/存货/在建工程也必须补填(否则恒为 0)
    assert res.accounts_receivable == pytest.approx(130.0 * YI)
    assert res.inventory == pytest.approx(281.83 * YI)
    assert res.construction_in_progress == pytest.approx(13.38 * YI)


def test_parse_cash_flow_sina_only_fills_empty_fields():
    """已解析的字段不被新浪覆盖(东财仅 capex 列名未匹配时, OCF/ICF 仍保留东财值)。"""
    adp = AkshareFinancialAdapter()
    res = FinancialData(symbol="000651", operating_cf=999.0 * YI, investing_cf=-1.0 * YI)
    adp._parse_cash_flow_sina(res, _cf_df())
    assert res.operating_cf == pytest.approx(999.0 * YI)  # 未覆盖
    assert res.capex == pytest.approx(17.17 * YI)          # 补全


def test_parse_income_statement_keeps_negative_eps():
    """亏损股 EPS 为负, 早先 `if eps > 0` 会静默丢弃, 现应保留负值。"""
    adp = AkshareFinancialAdapter()
    res = FinancialData(symbol="900001")
    adp._parse_income_statement(res, pd.DataFrame([{"BASIC_EPS": -1.5}]))
    assert res.eps == pytest.approx(-1.5)


def test_parse_income_sina_keeps_negative_eps():
    adp = AkshareFinancialAdapter()
    res = FinancialData(symbol="900001")
    adp._parse_income_sina(res, pd.DataFrame([
        {"报告日": "20251231", "基本每股收益": -0.8},
    ]))
    assert res.eps == pytest.approx(-0.8)


def test_parse_cash_flow_sina_no_annual_rows_is_noop():
    adp = AkshareFinancialAdapter()
    res = FinancialData(symbol="000651")
    df = _cf_df()
    df["报告日"] = ["20250930", "20240930"]  # 无年报行
    adp._parse_cash_flow_sina(res, df)
    assert res.capex == 0.0
    assert res.operating_cf == 0.0


def test_parse_cash_flow_sina_dividend_variant_column():
    """新浪现金流量表分红列名存在多种写法(「所支付的现金」/「支付的现金」等),
    逐候选精确匹配,避免个别股票因列名微差导致 dividend_paid=0 → 股息率 N/A。"""
    adp = AkshareFinancialAdapter()
    res = FinancialData(symbol="600519")
    df = pd.DataFrame([
        {"报告日": "20251231",
         "购建固定资产、无形资产和其他长期资产所支付的现金": 17.17 * YI,
         "经营活动产生的现金流量净额": 463.83 * YI,
         "投资活动产生的现金流量净额": -485.99 * YI,
         "筹资活动产生的现金流量净额": 86.06 * YI,
         # 变体列名: 缺「所」
         "分配股利、利润或偿付利息支付的现金": 200.0 * YI},
    ])
    adp._parse_cash_flow_sina(res, df)
    assert res.dividend_paid == pytest.approx(200.0 * YI)


def test_fetch_source_label_em_softblocked_uses_sina():
    """东财三表原始数据全空(被风控软封)→ 整体回退新浪, source 标记新浪兜底。"""
    adp = AkshareFinancialAdapter()
    adp._cache = _NoCache()
    adp._get_raw_statements = AsyncMock(return_value=(None, None, None))
    with patch.object(adp, "_sync_cashflow_sina", return_value=_cf_df()), \
         patch.object(adp, "_sync_income_sina", return_value=_income_df()), \
         patch.object(adp, "_sync_balance_sina", return_value=_balance_df()):
        res = asyncio.run(adp.fetch("000651"))
    assert res.source == "akshare(新浪sina兜底-东财软封)"
    # 新浪补全核心字段
    assert res.capex == pytest.approx(17.17 * YI)
    assert res.net_profit == pytest.approx(288.63 * YI)
    assert res.total_assets == pytest.approx(3913.72 * YI)
    # 余额表三项也必须从新浪补填
    assert res.accounts_receivable == pytest.approx(130.0 * YI)
    assert res.inventory == pytest.approx(281.83 * YI)
    assert res.construction_in_progress == pytest.approx(13.38 * YI)


def test_fetch_source_label_em_ok_pure_em():
    """东财可解析出核心字段 → source 标记东方财富, 不触发任何新浪兜底。"""
    adp = AkshareFinancialAdapter()
    adp._cache = _NoCache()
    inc = pd.DataFrame([{"REPORT_DATE": "20251231", "REPORT_TYPE": "年报"}])
    bs = pd.DataFrame([{"REPORT_DATE": "20251231", "REPORT_TYPE": "年报"}])
    cf = pd.DataFrame([{"REPORT_DATE": "20251231", "REPORT_TYPE": "年报"}])

    def fake_income(result, _df):
        result.net_profit = 288.63 * YI
        result.revenue = 1704.47 * YI

    def fake_balance(result, _df):
        result.total_assets = 3913.72 * YI
        # 核心资产负债表字段齐备 → 不再触发新浪资产负债表兜底
        result.accounts_receivable = 159.87 * YI
        result.inventory = 281.83 * YI
        result.monetary_funds = 1105.53 * YI

    def fake_cash(result, _df):
        result.capex = 17.17 * YI
        result.operating_cf = 463.83 * YI

    adp._get_raw_statements = AsyncMock(return_value=(inc, bs, cf))
    with patch.object(adp, "_parse_income_statement", fake_income), \
         patch.object(adp, "_parse_balance_sheet", fake_balance), \
         patch.object(adp, "_parse_cash_flow", fake_cash), \
         patch.object(adp, "_sync_balance_sina", lambda _s: None):
        # 合同负债补充兜底走新浪时(东财 mock 未设 contract_liabilities),
        # 静默回退且**不**污染 source 标记 → 仍为纯东财。
        res = asyncio.run(adp.fetch("000651"))
    assert res.source == "akshare(东方财富)"
    assert res.capex == pytest.approx(17.17 * YI)
