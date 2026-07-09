"""Collector orchestrator — manages concurrent collection, error handling, result aggregation.

Extracted from CompositeStockAnalysisRepository (audit P2.1/P2.3) to separate
orchestration concerns from repository port adaptation. The orchestrator owns
the collectors and produces an immutable CollectPayload; the repository is now
a thin adapter delegating to this class.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from typing import Any

import httpx

from aimoon.adapters.driven.common.timing import logphase
from aimoon.core.application.progress import CliProgressReporter, ProgressReporter
from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
from aimoon.core.domain.entities.capital_flow import CapitalFlowData
from aimoon.core.domain.entities.financial import FinancialData, QuarterlyFinancialData
from aimoon.core.domain.entities.kline import KlineData
from aimoon.core.domain.entities.quote import StockQuote
from aimoon.core.domain.entities.research import ResearchReportData
from aimoon.core.domain.services.symbols import resolve_market
from aimoon.core.domain.value_objects.collect_result import CollectResult

from .capital_flow import CapitalFlowCollector
from .kline import KlineCollector
from .quote import QuoteCollector
from .research_report import ResearchReportCollector
from .social_orchestrator import SocialMediaOrchestrator

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class CollectPayload:
    """采集阶段产出 — 不可变。

    一次 orchestrate() 调用返回全部数据，消除旧的
    ``collect_all() + get_collect_results()`` 二次调用时序依赖。
    """

    stock_analysis: StockAnalysis
    results: tuple[CollectResult, ...]
    elapsed_ms: int


class CollectorOrchestrator:
    """编排器 — 管理并发采集、错误处理、结果聚合。

    持有各采集器引用 + ProgressReporter，orchestrate() 一次调用产出
    CollectPayload。单个采集器失败不阻塞管线（broad-except 契约）。
    """

    def __init__(
        self,
        quote_collector: QuoteCollector | None = None,
        financial_collector: Any = None,
        kline_collector: KlineCollector | None = None,
        capital_flow_collector: CapitalFlowCollector | None = None,
        research_collector: ResearchReportCollector | None = None,
        social_collector: SocialMediaOrchestrator | None = None,
        http_client: httpx.AsyncClient | None = None,
        reporter: ProgressReporter | None = None,
    ) -> None:
        self._http = http_client
        self._reporter = reporter or CliProgressReporter()
        self._quote_collector = quote_collector or QuoteCollector(client=http_client)
        self._financial_collector = financial_collector
        self._kline_collector = kline_collector or KlineCollector(client=http_client)
        self._capital_flow_collector = capital_flow_collector or CapitalFlowCollector(
            client=http_client
        )
        self._research_collector = research_collector or ResearchReportCollector()
        self._social_collector = social_collector or SocialMediaOrchestrator(
            http_client=self._http
        )
        self._last_results: tuple[CollectResult, ...] = ()

    @property
    def last_results(self) -> tuple[CollectResult, ...]:
        return self._last_results

    async def orchestrate(self, symbol: str, name: str = "") -> CollectPayload:
        """执行完整编排流程，返回不可变 CollectPayload。"""
        t0 = time.monotonic()
        results: list[CollectResult] = []

        try:
            stock_analysis = await self._collect_all_inner(symbol, name, results)
        finally:
            await self._quote_collector.aclose()

        self._last_results = tuple(results)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return CollectPayload(
            stock_analysis=stock_analysis,
            results=self._last_results,
            elapsed_ms=elapsed_ms,
        )

    async def _collect_all_inner(
        self, symbol: str, name: str, results: list[CollectResult]
    ) -> StockAnalysis:
        # Phase A: quote first (fast, needed by social for stock name)
        self._reporter.report(" 采集行情...")
        quote_result = await self._fetch_quote(symbol, name)
        quote = self._unwrap_quote(quote_result, symbol, name, results)
        stock_name = quote.name or name

        # Phase B: remaining 6 collectors + social, all parallel
        self._reporter.report(" 并行采集财务/K线/资金流/研报/社媒...")
        t0 = time.monotonic()
        with logphase("collectors(fin+kline+cf+research+history+social)"):
            gathered = await asyncio.gather(
                self._collect_financial(symbol),
                self._collect_quarterly_financial(symbol),
                self._kline_collector.fetch(symbol),
                self._capital_flow_collector.fetch(symbol),
                self._research_collector.fetch(symbol),
                self._collect_history_financial(symbol),
                self._social_collector.collect(symbol, stock_name),
                return_exceptions=True,
            )
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        financial = self._unwrap(
            gathered[0], FinancialData, symbol=symbol, platform="财务数据(年报)",
            ok=lambda d: d and d.report_period,
            msg=lambda d: f"   财务: 报告期 {d.report_period} | ROE: {d.roe}% [来源: {d.source}]",
            fail="   财务: 获取失败。", elapsed_ms=elapsed_ms, results=results,
        )
        quarterly = self._unwrap(
            gathered[1], QuarterlyFinancialData, symbol=symbol, platform="财务数据(季报)",
            ok=lambda d: d and d.report_period,
            msg=lambda d: (
                f"   季报: {d.report_period} | 营收 {d.revenue / 1e8:.1f}亿 ({d.revenue_yoy:+.1f}%)"
                f" [来源: {d.source}]"
            ),
            fail="   季报: 获取失败。", elapsed_ms=elapsed_ms, results=results,
        )
        kline = self._unwrap(
            gathered[2], KlineData, symbol=symbol, platform="K线数据",
            ok=lambda d: d and d.bars,
            msg=lambda d: f"   K线: {len(d.bars)}根 [{d.source}]",
            fail="   K线: 获取失败，技术分析将使用基础数据。",
            elapsed_ms=elapsed_ms, results=results,
        )
        capital_flow = self._unwrap(
            gathered[3], CapitalFlowData, symbol=symbol, platform="资金流向",
            ok=lambda d: d and d.source and d.source != "all_failed",
            msg=lambda d: f"   资金流: 主力5日 {d.main_net_5d / 1e8:.2f}亿 [{d.source}]",
            fail="   资金流: 获取失败。", elapsed_ms=elapsed_ms, results=results,
        )
        research = self._unwrap(
            gathered[4], ResearchReportData, symbol=symbol, platform="研报数据",
            ok=lambda d: d and d.total_count > 0,
            msg=lambda d: f"   研报: {d.total_count}条 [来源: {d.source}]",
            fail="   研报: 获取失败。", elapsed_ms=elapsed_ms, results=results,
        )

        history_raw = gathered[5]
        if isinstance(history_raw, Exception):
            logger.debug("[history] 历史财务采集失败: %s", history_raw)
            self._reporter.report("   历史财务: 获取失败")
            history: list[FinancialData] = []
        elif isinstance(history_raw, list) and (
            not history_raw or isinstance(history_raw[0], FinancialData)
        ):
            history = history_raw
            self._reporter.report(f"   历史财务: {len(history)} 年年报")
        else:
            history = []

        # Social result is at index 6
        all_posts: list = []
        social_raw = gathered[6]
        if isinstance(social_raw, Exception):
            logger.debug("[social] 社媒采集异常: %s", social_raw)
        elif isinstance(social_raw, tuple) and len(social_raw) == 2:
            all_posts, social_results = social_raw
            results.extend(social_results)

        return StockAnalysis(
            symbol=symbol,
            name=stock_name,
            market=resolve_market(symbol),
            quote=quote,
            financial=financial,
            quarterly_financial=quarterly,
            kline=kline,
            capital_flow=capital_flow,
            social_posts=tuple(all_posts or []),
            research=research,
            history_financial=history if isinstance(history, list) else [],
        )

    async def _fetch_quote(self, symbol: str, name: str) -> StockQuote:
        return await self._quote_collector.fetch(symbol, name=name)

    def _unwrap_quote(
        self, result: object, symbol: str, name: str, results: list[CollectResult]
    ) -> StockQuote:
        quote: StockQuote | None = None
        if not isinstance(result, Exception):
            quote = result  # type: ignore[assignment]
        if quote and quote.price > 0:
            info = f"{quote.name}: {quote.price} ({quote.change_pct:+.2f}%) PE={quote.pe}"
            self._reporter.report(f"   {info} [来源: {quote.source}]")
            results.append(
                CollectResult(platform="实时行情", status="success", count=1, elapsed_ms=0)
            )
            return quote
        error = str(result) if isinstance(result, Exception) else "价格为零"
        if isinstance(result, Exception):
            self._reporter.report(f"   行情: 获取失败 [{type(result).__name__}]")
        else:
            self._reporter.report("   行情: 获取失败（价格为零）")
        results.append(
            CollectResult(platform="实时行情", status="failed", count=0, elapsed_ms=0, error=error)
        )
        return StockQuote(symbol=symbol, name=name, source="获取失败")

    async def _collect_financial(self, symbol: str) -> FinancialData:
        if self._financial_collector is not None:
            return await self._financial_collector.fetch(symbol)
        return FinancialData(symbol=symbol)

    async def _collect_quarterly_financial(self, symbol: str) -> QuarterlyFinancialData:
        if self._financial_collector is not None:
            return await self._financial_collector.fetch_quarterly(symbol)
        return QuarterlyFinancialData(symbol=symbol)

    async def _collect_history_financial(self, symbol: str) -> list[FinancialData]:
        if self._financial_collector is not None and hasattr(
            self._financial_collector, "fetch_history"
        ):
            return await self._financial_collector.fetch_history(symbol)
        return []

    def _unwrap(
        self, result, factory, *, symbol, platform, ok, msg, fail,
        elapsed_ms: int = 0, results: list[CollectResult] | None = None,
    ):
        """解包 gather 结果：检查成功、报告状态、返回数据。"""
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
        self._reporter.report(label)
        if results is not None:
            results.append(
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
