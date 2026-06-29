"""组合式股票分析资源库 — 组合所有采集器。

CompositeStockAnalysisRepository 组合多个数据采集器，
实现 StockAnalysisRepository 端口接口，
对外提供统一的股票数据采集能力。
"""

from __future__ import annotations

import asyncio
import time

from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
from aimoon.core.domain.entities.capital_flow import CapitalFlowData
from aimoon.core.domain.entities.financial import FinancialData, QuarterlyFinancialData
from aimoon.core.domain.entities.kline import KlineData
from aimoon.core.domain.entities.quote import StockQuote
from aimoon.core.domain.entities.research import ResearchReportData
from aimoon.core.domain.repositories.stock_analysis_repo import (
    StockAnalysisRepository,
)
from aimoon.core.domain.services.symbols import resolve_market
from aimoon.core.domain.value_objects.collect_result import CollectResult

from .capital_flow import CapitalFlowCollector
from .kline import KlineCollector
from .quote import QuoteCollector
from .research_report import ResearchReportCollector
from .social_orchestrator import SocialMediaOrchestrator


class CompositeStockAnalysisRepository(StockAnalysisRepository):
    """组合式股票分析资源库。

    组合行情、财务、K线、资金流、研报、社媒等多个采集器，
    统一编排采集流程，返回完整的 StockAnalysis 聚合根。
    """

    def __init__(
        self,
        quote_collector: QuoteCollector | None = None,
        financial_collector=None,
        kline_collector: KlineCollector | None = None,
        capital_flow_collector: CapitalFlowCollector | None = None,
        research_collector: ResearchReportCollector | None = None,
        social_collector: SocialMediaOrchestrator | None = None,
    ) -> None:
        self._quote_collector = quote_collector or QuoteCollector()
        self._financial_collector = financial_collector
        self._kline_collector = kline_collector or KlineCollector()
        self._capital_flow_collector = capital_flow_collector or CapitalFlowCollector()
        self._research_collector = research_collector or ResearchReportCollector()
        self._social_collector = social_collector or SocialMediaOrchestrator()
        self._collect_results: list[CollectResult] = []

    async def collect_all(self, symbol: str, name: str = "") -> StockAnalysis:
        """采集指定股票的所有维度数据，返回完整聚合。

        Args:
            symbol: 6位股票代码
            name: 股票名称，可选

        Returns:
            StockAnalysis 聚合根实例
        """
        self._collect_results = []

        try:
            return await self._collect_all_inner(symbol, name)
        finally:
            # M6: ensure HTTP client is closed
            await self._quote_collector.aclose()

    async def _collect_all_inner(self, symbol: str, name: str) -> StockAnalysis:
        quote = await self._collect_quote(symbol, name)
        if quote.name:
            name = quote.name

        print(" 并行采集财务(年报+季报)/K线/资金流/研报...")
        t0 = time.monotonic()
        results = await asyncio.gather(
            self._collect_financial(symbol),
            self._collect_quarterly_financial(symbol),
            self._kline_collector.fetch(symbol),
            self._capital_flow_collector.fetch(symbol),
            self._research_collector.fetch(symbol),
            return_exceptions=True,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        financial = self._unwrap(
            results[0],
            FinancialData,
            symbol=symbol,
            platform="财务数据(年报)",
            ok=lambda d: d and d.report_period,
            msg=lambda d: f"   财务: 报告期 {d.report_period} | ROE: {d.roe}% [来源: {d.source}]",
            fail="   财务: 获取失败。",
            elapsed_ms=elapsed_ms,
        )
        quarterly = self._unwrap(
            results[1],
            QuarterlyFinancialData,
            symbol=symbol,
            platform="财务数据(季报)",
            ok=lambda d: d and d.report_period,
            msg=lambda d: (
                f"   季报: {d.report_period} | 营收 {d.revenue / 1e8:.1f}亿 ({d.revenue_yoy:+.1f}%)"
                f" [来源: {d.source}]"
            ),
            fail="   季报: 获取失败。",
            elapsed_ms=elapsed_ms,
        )
        kline = self._unwrap(
            results[2],
            KlineData,
            symbol=symbol,
            platform="K线数据",
            ok=lambda d: d and d.bars,
            msg=lambda d: f"   K线: {len(d.bars)}根 [{d.source}]",
            fail="   K线: 获取失败，技术分析将使用基础数据。",
            elapsed_ms=elapsed_ms,
        )
        capital_flow = self._unwrap(
            results[3],
            CapitalFlowData,
            symbol=symbol,
            platform="资金流向",
            ok=lambda d: d and d.source and d.source != "all_failed",
            msg=lambda d: f"   资金流: 主力5日 {d.main_net_5d / 1e8:.2f}亿 [{d.source}]",
            fail="   资金流: 获取失败。",
            elapsed_ms=elapsed_ms,
        )
        research = self._unwrap(
            results[4],
            ResearchReportData,
            symbol=symbol,
            platform="研报数据",
            ok=lambda d: d and d.total_count > 0,
            msg=lambda d: f"   研报: {d.total_count}条 [来源: {d.source}]",
            fail="   研报: 获取失败。",
            elapsed_ms=elapsed_ms,
        )

        all_posts, social_results = await self._social_collector.collect(symbol, name)
        self._collect_results.extend(social_results)

        return StockAnalysis(
            symbol=symbol,
            name=name,
            market=resolve_market(symbol),
            quote=quote,
            financial=financial,
            quarterly_financial=quarterly,
            kline=kline,
            capital_flow=capital_flow,
            social_posts=all_posts,
            research=research,
        )

    async def get_collect_results(self) -> list[CollectResult]:
        """获取各数据源的采集结果详情。

        Returns:
            各平台采集结果列表
        """
        return self._collect_results

    async def _collect_quote(self, symbol: str, name: str) -> StockQuote:
        print(" 采集实时行情...")
        try:
            quote = await self._quote_collector.fetch(symbol, name=name)
            info = f"{quote.name}: {quote.price} ({quote.change_pct:+.2f}%) PE={quote.pe}"
            print(f"   {info} [来源: {quote.source}]")
            self._collect_results.append(
                CollectResult(
                    platform="实时行情",
                    status="success",
                    count=1,
                    elapsed_ms=0,
                )
            )
        except Exception as e:
            quote = StockQuote(symbol=symbol, name=name, source="获取失败")
            print(f"   行情: 获取失败 [{type(e).__name__}]")
            self._collect_results.append(
                CollectResult(
                    platform="实时行情",
                    status="failed",
                    count=0,
                    elapsed_ms=0,
                    error=str(e),
                )
            )
        return quote

    async def _collect_financial(self, symbol: str) -> FinancialData:
        if self._financial_collector is not None:
            return await self._financial_collector.fetch(symbol)
        return FinancialData(symbol=symbol)

    async def _collect_quarterly_financial(self, symbol: str) -> QuarterlyFinancialData:
        if self._financial_collector is not None:
            return await self._financial_collector.fetch_quarterly(symbol)
        return QuarterlyFinancialData(symbol=symbol)

    def _unwrap(
        self,
        result,
        factory,
        *,
        symbol: str,
        platform: str,
        ok,
        msg,
        fail,
        elapsed_ms: int = 0,
    ):
        """解包 gather 结果：检查成功、打印状态、返回数据。

        同时记录 CollectResult 到 self._collect_results。
        """
        data = factory(symbol=symbol)
        status = "success"
        error = ""
        if isinstance(result, Exception):
            status = "failed"
            error = str(result)
        else:
            data = result
        is_ok = ok(data)
        if not is_ok and not isinstance(result, Exception):
            status = "empty"
        label = msg(data) if is_ok else fail
        print(label)
        self._collect_results.append(
            CollectResult(
                platform=platform,
                status=status,
                count=(
                    len(getattr(data, "bars", []))
                    if hasattr(data, "bars")
                    else getattr(data, "total_count", 0)
                ),
                elapsed_ms=elapsed_ms,
                error=error,
            )
        )
        return data
