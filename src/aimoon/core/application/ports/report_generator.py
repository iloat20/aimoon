"""报告生成器接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
from aimoon.core.domain.value_objects.analysis_report import AnalysisReport
from aimoon.core.domain.value_objects.collect_result import CollectResult


class ReportGenerator(ABC):
    """报告生成器端口。

    负责将分析结果渲染为最终的 HTML 报告文件。
    """

    @abstractmethod
    def generate(
        self,
        stock_info: StockAnalysis,
        analysis: AnalysisReport,
        collect_results: list[CollectResult],
        output_dir: str | None = None,
        credibility: dict | None = None,
    ) -> Path:
        """生成 HTML 分析报告。

        Args:
            stock_info: 股票信息实体
            analysis: AI 分析结果实体
            collect_results: 各数据源采集结果
            output_dir: 输出目录，可选
            credibility: 可选的数据可信度摘要，形状为
                {"checked", "corrected", "uncertain"} 或 {"skipped": "..."}

        Returns:
            生成的 HTML 报告文件路径
        """
