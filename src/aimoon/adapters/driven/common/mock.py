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
        summary=f"【示例占位】本报告为 mock 模式生成，{name}({symbol}) 的所有数据与结论"
        f"均为演示占位，不代表真实分析，请勿据此决策。",
        data_warnings=[
            "本报告为 mock 模式生成，全部数据为模拟/占位值，不代表真实市场",
            "如需真实分析，请去掉 --mock 并配置 API Key 后重新运行",
            "示例文本不含任何方向性投资判断",
        ],
        data_confidence={
            "行情数据": "高",
            "财务数据": "中",
            "资金流向": "中",
            "研报数据": "低",
            "社媒舆情": "低",
        },
        investment_advice=(
            "【示例占位·非投资建议】本报告为 mock 模式生成，仅用于演示报告排版与链路，"
            "不构成任何投资建议。真实分析请去掉 --mock 后重新运行。"
        ),
        report_text=(
            f"> ⚠️ **本报告为 mock 模式生成的示例占位内容**，"
            f"下列文字与数据均为演示用途，不代表 {name}（{symbol}）的真实情况，请勿据此决策。\n\n"
            f"## 一、公司概况与业务分析\n\n"
            f"（示例占位）此处在真实模式下会输出 {name}（{symbol}）的主营业务构成、"
            f"分地区/分产品收入拆分与竞争格局分析。mock 模式不含真实数据。\n\n"
            f"## 二、财务健康度评估\n\n"
            f"（示例占位）此处在真实模式下会基于确定性财务底稿输出营收/净利/ROE/"
            f"资产负债率/现金流等指标及其同比，并内联标注来源表。\n\n"
            f"## 三、资金面分析\n\n"
            f"（示例占位）此处在真实模式下会输出主力资金净流入、换手率、渠道代理指标等。"
            f"北向资金已停止披露，真实报告不会给出北向方向性判断。\n\n"
            f"## 四、行业与市场前景\n\n"
            f"（示例占位）此处在真实模式下会输出行业景气度、可比公司 PE/PB 中位数对比等。\n\n"
            f"## 五、投资建议与风险提示\n\n"
            f"（示例占位）mock 模式不给出评级与建议。请去掉 --mock 获取真实分析。\n"
        ),
    )
