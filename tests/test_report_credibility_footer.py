"""Task 8：报告末尾「数据可信度」页脚渲染回归测试。

验证 generator 把 credibility 摘要写入 context 后，
index.html 在报告末尾条件渲染「数据可信度」卡片：
- 正常：已程序化核对指标声明 / 自动修正数 / 仍存疑清单 + 核对范围说明
- skipped：显示「数据自检未执行：<原因>」
"""

from aimoon.adapters.driven.report.generator import HtmlReportGenerator
from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
from aimoon.core.domain.entities.quote import StockQuote
from aimoon.core.domain.value_objects.analysis_report import AnalysisReport


def _render_report(credibility: dict | None = None) -> str:
    gen = HtmlReportGenerator()
    stock = StockAnalysis(symbol="600519", name="测试", quote=StockQuote())
    ctx = gen._build_context(stock, AnalysisReport(), [], credibility=credibility)
    return gen._env.get_template("index.html").render(**ctx)


def test_credibility_footer_renders():
    """正常摘要：卡片标题与核对数均出现在 HTML 中。"""
    html = _render_report({"checked": 12, "corrected": 1, "uncertain": ["XX 存疑"]})
    assert "数据可信度" in html
    assert "12" in html
    # C4 诚实页脚：标签由「核对事实数」改为「已程序化核对指标声明」,并附核对范围说明。
    assert "已程序化核对指标声明" in html
    assert "自动修正数" in html
    assert "XX 存疑" in html
    assert "核对范围" in html


def test_credibility_footer_skipped():
    """护栏被跳过：显示「数据自检未执行」。"""
    html = _render_report({"skipped": "reconcile disabled"})
    assert "数据可信度" in html
    assert "数据自检未执行" in html
    assert "reconcile disabled" in html
    # skipped 时不渲染核对数统计块
    assert "已程序化核对指标声明" not in html


def test_credibility_empty_uncertain_shows_none():
    """uncertain 为空时显示「无」。"""
    html = _render_report({"checked": 5, "corrected": 0, "uncertain": []})
    assert "数据可信度" in html
    assert "无" in html


def test_no_credibility_hides_footer():
    """credibility 为空(生产降级)时不渲染页脚卡片。"""
    html = _render_report()
    assert "数据可信度" not in html
