"""分析报告值对象。

AnalysisReport 是一个值对象，概念上不可变。
它是AI生成的完整分析报告，作为值对象存在于领域模型中，
没有独立的生命周期，依附于 StockAnalysis 聚合根或作为独立输出。
"""

from datetime import datetime

from pydantic import BaseModel, Field


class AnalysisReport(BaseModel):
    """完整的AI分析报告。"""

    model_config = {"frozen": True}

    symbol: str = ""
    name: str = ""
    generated_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    summary: str = ""

    data_warnings: list[str] = Field(default_factory=list)
    data_confidence: dict[str, str] = Field(default_factory=dict)

    credibility: dict[str, object] = Field(default_factory=dict)

    investment_advice: str = ""

    report_text: str = ""
