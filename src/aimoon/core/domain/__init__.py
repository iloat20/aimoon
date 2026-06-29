"""领域层模块。

统一导出所有领域模型，包括实体、值对象、聚合根和领域服务。
"""

from .aggregates import StockAnalysis
from .entities import (
    CapitalFlowData,
    FinancialData,
    KlineData,
    ResearchReport,
    ResearchReportData,
    SocialPost,
    StockQuote,
)
from .repositories import StockAnalysisRepository
from .services import (
    calculate_total_score,
    capital_flow_score,
    fundamental_score,
    news_score,
    resolve_market,
    resolve_symbol,
    to_sina_symbol,
    to_xueqiu_symbol,
)
from .value_objects import (
    AnalysisReport,
    CollectResult,
    DimensionScore,
    FinancialReportData,
    KlineBar,
)

__all__ = [
    "StockAnalysis",
    "StockAnalysisRepository",
    "StockQuote",
    "FinancialData",
    "KlineData",
    "CapitalFlowData",
    "ResearchReportData",
    "ResearchReport",
    "SocialPost",
    "KlineBar",
    "DimensionScore",
    "AnalysisReport",
    "CollectResult",
    "FinancialReportData",
    "fundamental_score",
    "capital_flow_score",
    "news_score",
    "calculate_total_score",
    "resolve_market",
    "resolve_symbol",
    "to_sina_symbol",
    "to_xueqiu_symbol",
]
