"""数据采集器适配器 (Collectors)

各类数据源采集适配器的集合。

职责：
- 从不同数据源采集股票相关数据
- 实现数据采集端口接口
- 处理数据源的特定协议和格式
- 数据预处理和清洗

数据源类型：
- K线数据采集
- 行情数据采集
- 资金流向数据采集
- 研究报告采集
- 社交媒体数据采集
"""

from .base import BaseCollector, CollectorRegistry, DataCollector
from .capital_flow import CapitalFlowCollector
from .cninfo import CninfoCollector
from .composite_repo import CompositeStockAnalysisRepository
from .eastmoney_playwright import GubaCollector
from .kline import KlineCollector
from .mock import (
    mock_analysis_report,
    mock_financial,
    mock_quote,
    mock_social_posts,
    mock_stock_analysis,
)
from .mock_repo import MockStockAnalysisRepository
from .quote import QuoteCollector
from .research_report import ResearchReportCollector
from .social_orchestrator import SocialMediaOrchestrator
from .wechat import WechatCollector

__all__ = [
    "BaseCollector",
    "CapitalFlowCollector",
    "CollectorRegistry",
    "CninfoCollector",
    "CompositeStockAnalysisRepository",
    "DataCollector",
    "GubaCollector",
    "KlineCollector",
    "MockStockAnalysisRepository",
    "QuoteCollector",
    "ResearchReportCollector",
    "SocialMediaOrchestrator",
    "WechatCollector",
    "mock_analysis_report",
    "mock_financial",
    "mock_quote",
    "mock_social_posts",
    "mock_stock_analysis",
]
