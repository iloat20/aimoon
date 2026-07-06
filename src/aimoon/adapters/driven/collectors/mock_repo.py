"""Mock 股票分析资源库 — 实现 StockAnalysisRepository 接口。

使用 mock 数据生成器提供模拟数据，用于测试和演示。
"""

from __future__ import annotations

from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
from aimoon.core.domain.repositories.stock_analysis_repo import (
    StockAnalysisRepository,
)
from aimoon.core.domain.value_objects.collect_result import CollectResult

from .mock import mock_stock_analysis


class MockStockAnalysisRepository(StockAnalysisRepository):
    """Mock 股票分析资源库。

    使用 mock 数据生成器实现 StockAnalysisRepository 接口，
    提供完整的模拟数据用于测试和演示。
    """

    def __init__(self) -> None:
        self._collect_results: list[CollectResult] = []

    async def collect_all(self, symbol: str, name: str = "") -> StockAnalysis:
        """采集指定股票的所有维度数据（mock 实现）。

        Args:
            symbol: 6位股票代码
            name: 股票名称，可选

        Returns:
            StockAnalysis 聚合根实例
        """
        print(f" [Mock] 生成 {symbol} 的模拟数据...")
        stock_analysis = mock_stock_analysis(symbol)
        if name:
            stock_analysis.name = name

        platforms = ["雪球", "东方财富股吧", "微信公众号"]
        self._collect_results = [
            CollectResult(
                platform=p,
                status="success",
                count=len([x for x in stock_analysis.social_posts if x.platform == p]),
                elapsed_ms=50,
            )
            for p in platforms
        ]

        return stock_analysis

    async def get_collect_results(self) -> list[CollectResult]:
        """获取各数据源的采集结果详情。

        Returns:
            各平台采集结果列表
        """
        return self._collect_results
