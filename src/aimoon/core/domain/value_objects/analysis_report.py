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

    # 系统预渲染数据表(财务时序/同行对比/估值/FCF/情景/舆情/健康/分业务),
    # 与 report_text 分离,由报告模板渲染为独立的「数据底稿」卡片,置于 AI 报告之后(文末附录)。
    data_appendix_md: str = ""

    # 估值安全边际「三列情景卡片」可信 HTML 片段(乐观/中性/悲观),
    # 由 table_renderer.render_margin_of_safety_cards 生成,报告模板以 |safe 注入前端。
    margin_of_safety_html: str = ""
