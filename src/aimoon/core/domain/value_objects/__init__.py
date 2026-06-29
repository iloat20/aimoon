"""领域值对象模块。"""

from .analysis_report import AnalysisReport
from .collect_result import CollectResult
from .dimension_score import DimensionScore
from .financial_report import FinancialReportData
from .kline_bar import KlineBar

__all__ = [
    "KlineBar",
    "FinancialReportData",
    "DimensionScore",
    "CollectResult",
    "AnalysisReport",
]
