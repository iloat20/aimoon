"""股债性价比信号灯(第三级体验增强)回归测试。

验证 domain 层 build_equity_bond_signal 的确定性计算与 index.html 卡片渲染:
- 正常:股息率 / 风险溢价 / FCF 覆盖 / 历史分位 / 信号 均正确
- 缺失:分红或市值缺失 → 股息率 None、信号 N/A
- 分位:用历史 dividend_paid 归一化序列计算百分位
- HTML:卡片与关键标签出现在渲染结果中
"""

from aimoon.adapters.driven.report.generator import HtmlReportGenerator
from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
from aimoon.core.domain.entities.financial import FinancialData
from aimoon.core.domain.entities.quote import StockQuote
from aimoon.core.domain.services.valuation_signals import CGB_10Y, build_equity_bond_signal
from aimoon.core.domain.value_objects.analysis_report import AnalysisReport


def _sig(dividend_paid=0.0, market_cap=0.0, ocf=0.0, capex=0.0, history=None):
    q = StockQuote(market_cap=market_cap)
    fin = FinancialData(dividend_paid=dividend_paid, operating_cf=ocf, capex=capex)
    return build_equity_bond_signal(q, fin, history)


def test_normal_green_signal():
    """格力样例:股息率≈8.2%,风险溢价+5.7% → 🟢 极度低估。"""
    # DY = 181.5e8 / 2213e8 ≈ 0.082
    s = _sig(dividend_paid=181.5e8, market_cap=2213e8, ocf=463.8e8, capex=17.17e8)
    assert abs(s["dividend_yield"] - 0.0820) < 1e-3
    assert abs(s["yield_vs_cgb"] - (0.0820 - CGB_10Y)) < 1e-3
    # FCF 覆盖 = (463.8 - 17.17) / 181.5 ≈ 2.46x
    assert abs(s["fcf_cover"] - 2.46) < 0.05
    assert s["signal"].startswith("🟢")
    assert s["percentile"] is None  # 无 history


def test_missing_dividend_na():
    """分红缺失 → 股息率 None、信号 N/A。"""
    s = _sig(dividend_paid=0.0, market_cap=2213e8)
    assert s["dividend_yield"] is None
    assert s["yield_vs_cgb"] is None
    assert s["signal"] == "N/A"


def test_low_yield_red_signal():
    """股息率仅 1% → 风险溢价负 → 🔴 股贵于债。"""
    s = _sig(dividend_paid=22.13e8, market_cap=2213e8)
    assert abs(s["dividend_yield"] - 0.01) < 1e-4
    assert s["signal"].startswith("🔴")


def test_percentile_computed():
    """历史 2 年 + 当前,当前股息率最高 → 分位 100%。"""
    hist = [
        FinancialData(dividend_paid=100e8),  # 归一化 100/2213e8
        FinancialData(dividend_paid=150e8),  # 归一化 150/2213e8
    ]
    # 当前 181.5e8 为序列最高
    s = _sig(dividend_paid=181.5e8, market_cap=2213e8, history=hist)
    assert s["percentile"] is not None
    assert s["sample_years"] == 3
    assert abs(s["percentile"] - 100.0) < 1e-6


def test_html_card_renders():
    """index.html 渲染出股债性价比卡片与关键标签。"""
    gen = HtmlReportGenerator()
    q = StockQuote(market_cap=2213e8)
    fin = FinancialData(dividend_paid=181.5e8, operating_cf=463.8e8, capex=17.17e8)
    stock = StockAnalysis(symbol="000651", name="格力电器", quote=q, financial=fin)
    ctx = gen._build_context(stock, AnalysisReport(), [])
    html = gen._env.get_template("index.html").render(**ctx)
    assert "股债性价比信号灯" in html
    assert "股息率" in html
    assert "10Y 国债收益率" in html
    assert "风险溢价" in html
    assert "FCF 覆盖分红" in html
