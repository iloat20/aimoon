"""组合式股票分析资源库 — 瘦仓库，委托给 CollectorOrchestrator。

CompositeStockAnalysisRepository 实现 StockAnalysisRepository 端口，
内部委托给 CollectorOrchestrator（编排逻辑已提取，audit P2.1）。
保留 get_collect_results() 向后兼容。
"""

from __future__ import annotations

import logging

import httpx

from aimoon.adapters.driven.common.progress import ProgressReporter
from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
from aimoon.core.domain.repositories.stock_analysis_repo import (
    StockAnalysisRepository,
)
from aimoon.core.domain.value_objects.collect_result import CollectResult

from .orchestrator import CollectorOrchestrator

logger = logging.getLogger(__name__)


class CompositeStockAnalysisRepository(StockAnalysisRepository):
    """瘦仓库 — 委托给 CollectorOrchestrator，实现端口接口。"""

    def __init__(
        self,
        quote_collector=None,
        financial_collector=None,
        kline_collector=None,
        capital_flow_collector=None,
        research_collector=None,
        social_collector=None,
        http_client: httpx.AsyncClient | None = None,
        reporter: ProgressReporter | None = None,
    ) -> None:
        self._orchestrator = CollectorOrchestrator(
            quote_collector=quote_collector,
            financial_collector=financial_collector,
            kline_collector=kline_collector,
            capital_flow_collector=capital_flow_collector,
            research_collector=research_collector,
            social_collector=social_collector,
            http_client=http_client,
            reporter=reporter,
        )

    async def collect_all(self, symbol: str, name: str = "") -> StockAnalysis:
        """采集指定股票的所有维度数据，返回完整聚合。"""
        payload = await self._orchestrator.orchestrate(symbol, name)
        return payload.stock_analysis

    async def get_collect_results(self) -> list[CollectResult]:
        """获取各数据源的采集结果详情（向后兼容）。"""
        return list(self._orchestrator.last_results)

    async def close(self) -> None:
        """关闭底层采集器资源（浏览器等），应在同一事件循环内调用。"""
        await self._orchestrator.close()
