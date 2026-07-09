"""Mock analysis report generator — shared across adapters.

``mock_analysis_report`` was previously in ``collectors/mock.py``, but
``ai/analyzer.py`` imports it — a cross-adapter dependency (audit P2.3).
Moved here so both ``collectors/`` and ``ai/`` depend on ``common/`` only.
"""

from __future__ import annotations

from aimoon.core.domain.value_objects.analysis_report import AnalysisReport


def mock_analysis_report(symbol: str, name: str) -> AnalysisReport:
    """Generate mock AI analysis report."""
    return AnalysisReport(
        symbol=symbol,
        name=name,
        summary=f"综合来看，{name}({symbol})基本面表现稳健，资金面偏暖，"
        f"建议投资者保持关注，逢低布局。",
        data_warnings=[
            "部分财务数据为模拟数据，仅供参考",
            "研报数据样本量较小，统计结论可能存在偏差",
            "社媒数据为随机生成，不代表真实市场情绪",
        ],
        data_confidence={
            "行情数据": "高",
            "财务数据": "中",
            "资金流向": "中",
            "研报数据": "低",
            "社媒舆情": "低",
        },
        investment_advice=(
            "【免责声明】本报告由AI自动生成，仅供参考，不构成任何投资建议。"
            "投资有风险，入市需谨慎。请结合自身情况独立决策。"
        ),
        report_text=(
            f"## 一、公司概况与业务分析\n\n"
            f"{name}（{symbol}）是行业内的龙头企业，主营业务涵盖多个领域。"
            f"公司在行业内具有较强的竞争优势和品牌影响力。\n\n"
            f"## 二、财务健康度评估\n\n"
            f"公司财务状况良好，营收和净利润保持稳定增长。ROE处于较高水平，"
            f"现金流充裕，资产负债率在合理范围内。\n\n"
            f"## 三、资金面分析\n\n"
            f"近期主力资金呈净流入状态，北向资金小幅增持。"
            f"资金面整体偏暖，有助于股价企稳回升。\n\n"
            f"## 四、行业与市场前景\n\n"
            f"行业整体处于稳定增长阶段，公司作为龙头企业有望持续受益。"
            f"市场关注度适中，机构持仓比例合理。\n\n"
            f"## 五、投资建议与风险提示\n\n"
            f"评级：【中性持有】\n"
            f"建议投资者保持关注，在回调时逢低布局。\n"
        ),
    )
