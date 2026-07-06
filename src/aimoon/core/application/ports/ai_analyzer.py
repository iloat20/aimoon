"""AI 分析器接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
from aimoon.core.domain.value_objects.analysis_report import AnalysisReport


class AIAnalyzer(ABC):
    """AI 分析器端口。

    负责对股票数据进行 AI 综合分析，生成分析报告。
    """

    @abstractmethod
    async def analyze(
        self,
        stock_info: StockAnalysis,
        reports: dict | None = None,
        financial_md_path: Path | None = None,
        *,
        use_pipeline_v2: bool = False,
        use_fast: bool = False,
        use_single_call: bool = False,
        use_ultra_fast: bool = False,
    ) -> AnalysisReport:
        """对股票信息进行 AI 分析。

        Args:
            stock_info: 聚合的股票信息实体
            reports: 财务报告原始数据，可选
            financial_md_path: 财务报告 MD 文件路径，可选
            use_pipeline_v2: 启用 v2 两阶段 pipeline
            use_fast: v2 pipeline 跳过 ANALYSIS 自检(更快输出)
            use_single_call: 实验性 single-call 模式(合并 ANALYSIS+self-check+COMPILE)
            use_ultra_fast: 极限快模式(跳过自检+COMPILE,初稿即终稿)

        Returns:
            AnalysisReport AI 分析结果实体
        """
