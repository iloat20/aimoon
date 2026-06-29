"""维度评分数值对象。

DimensionScore 是一个值对象，概念上不可变。
它表示单个分析维度的评分和解释，没有独立的唯一标识，
作为 AnalysisReport 值对象的组成部分存在。
"""

from pydantic import BaseModel


class DimensionScore(BaseModel):
    """单个分析维度，包含评分和解释。"""

    model_config = {"frozen": True}

    name: str = ""
    score: int = 0
    max_score: int = 5
    weight: float = 0.0
    analysis: str = ""
