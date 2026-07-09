"""回归测试：财务数据缺失时报告渲染兜底。

第十轮修复：基本面卡片在财务数据为空（report_period 被清空）时，
不再无条件渲染 "0.00亿 / 0.00%"，而是显示「财务数据暂不可用」，
与资金面卡片的降级兜底对齐。
"""

from aimoon.adapters.driven.report.generator import HtmlReportGenerator
from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
from aimoon.core.domain.entities.capital_flow import CapitalFlowData
from aimoon.core.domain.entities.financial import FinancialData
from aimoon.core.domain.entities.kline import KlineData
from aimoon.core.domain.entities.quote import StockQuote
from aimoon.core.domain.value_objects.analysis_report import AnalysisReport


def _render(stock: StockAnalysis) -> str:
    gen = HtmlReportGenerator()
    ctx = gen._build_context(stock, AnalysisReport(), [])
    return gen._env.get_template("index.html").render(**ctx)


def test_empty_financial_shows_unavailable():
    """report_period 为空（采集失败/全零）时显示「财务数据暂不可用」，
    且不渲染伪数据「0.00 亿」。"""
    stock = StockAnalysis(
        symbol="600519",
        name="测试",
        quote=StockQuote(),
        financial=FinancialData(symbol="600519"),  # 全 0，report_period=""
        capital_flow=CapitalFlowData(symbol="600519"),
        kline=KlineData(symbol="600519", bars=[]),
    )
    html = _render(stock)
    assert "财务数据暂不可用" in html
    # 财务营收/净利润格子在无数据时不再输出 "0.00 亿"
    assert "0.00 亿" not in html


def test_populated_financial_renders_figures():
    """有真实财务数据时正常渲染营收等字段，不显示「暂不可用」。"""
    stock = StockAnalysis(
        symbol="600519",
        name="测试",
        quote=StockQuote(),
        financial=FinancialData(
            symbol="600519",
            revenue=150_000_000_000,  # 1500 亿
            net_profit=75_000_000_000,
            roe=30.0,
            eps=59.49,
            revenue_yoy=15.0,
            net_profit_yoy=15.0,
            report_period="2025-12-31",
        ),
        capital_flow=CapitalFlowData(symbol="600519"),
        kline=KlineData(symbol="600519", bars=[]),
    )
    html = _render(stock)
    assert "财务数据暂不可用" not in html
    assert "1500.00" in html  # 营收渲染为 1500.00 亿
    assert "2025-12-31" in html  # 数据来源报告期
