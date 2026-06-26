"""Pipeline orchestrator — coordinates data collection, validation,
AI analysis, and report generation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .ai.analyzer import AIAnalyzer
from .collectors.mock import mock_analysis_report, mock_stock_info
from .config.settings import get_settings
from .models.social import CollectResult
from .models.stock import (
    CapitalFlowData,
    FinancialData,
    KlineData,
    ResearchReportData,
    StockInfo,
)
from .report.generator import ReportGenerator
from .utils import resolve_market


class PipelineOrchestrator:
    """Coordinates the full pipeline: collect → validate → analyze → report."""

    def __init__(self, output_dir: str | None = None) -> None:
        self._settings = get_settings()
        self._output_dir = output_dir
        self._all_posts: list[Any] = []
        self._collect_results: list[CollectResult] = []

    # ================================================================
    # Public API
    # ================================================================

    async def run(
        self, symbol: str, name: str, *, skip_ai: bool = False
    ) -> Path:
        """Run full pipeline with real data collection."""
        market = resolve_market(symbol)

        quote = await self._collect_quote(symbol, name)
        if quote.name:
            name = quote.name

        print(" 并行采集财务/K线/资金流/研报...")
        results = await asyncio.gather(
            self._collect_financial(symbol),
            self._collect_kline(symbol),
            self._collect_capital_flow(symbol),
            self._collect_research(symbol),
            return_exceptions=True,
        )

        financial = self._unwrap(results[0], FinancialData,
            ok=lambda d: d and d.report_period,
            msg=lambda d: (
                f"   财务: 报告期 {d.report_period} | ROE: {d.roe}%"
                f" [来源: {d.source}]"
            ),
            fail="   财务: 获取失败，使用空数据。可配置 XUEQIU_TOKEN 启用。")
        kline = self._unwrap(results[1], KlineData,
            ok=lambda d: d and d.bars,
            msg=lambda d: f"   K线: {len(d.bars)}根 [{d.source}]",
            fail="   K线: 获取失败，技术分析将使用基础数据。")
        capital_flow = self._unwrap(results[2], CapitalFlowData,
            ok=lambda d: d and d.source and d.source != "all_failed",
            msg=lambda d: (
                f"   资金流: 主力5日 {d.main_net_5d / 1e8:.2f}亿"
                f" [{d.source}]"
            ),
            fail="   资金流: 获取失败。")
        research = self._unwrap(results[3], ResearchReportData,
            ok=lambda d: d and d.total_count > 0,
            msg=lambda d: f"   研报: {d.total_count}条 [来源: {d.source}]",
            fail="   研报: 获取失败。")

        await self._collect_social(symbol, name)

        print(" 获取最新财务报告...")
        from .financial.annual_report import fetch_reports, save_report_as_md
        reports = await fetch_reports(symbol)
        md_path = save_report_as_md(symbol, name, reports)
        print(f"   财务数据MD: {md_path}")

        stock_info = StockInfo(
            symbol=symbol, name=name, market=market,
            quote=quote, financial=financial, kline=kline,
            capital_flow=capital_flow, social_posts=self._all_posts,
            research=research,
        )

        data_warnings, data_confidence = self._validate(stock_info)

        analysis = await self._analyze(stock_info, skip_ai, reports, md_path)
        analysis.data_warnings = data_warnings
        analysis.data_confidence = data_confidence

        return self._generate(stock_info, analysis)

    async def run_mock(self, symbol: str, name: str) -> Path:
        """Run full pipeline with mock data."""
        print(f" [Mock] 生成 {name}({symbol}) 的模拟分析报告...")

        stock_info = mock_stock_info(symbol)
        stock_info.name = name

        analysis = mock_analysis_report(symbol, name)

        platforms = ["雪球", "东方财富股吧", "今日头条", "微信公众号"]
        collect_results = [
            CollectResult(
                platform=p, status="success",
                count=len([x for x in stock_info.social_posts if x.platform == p]),
                elapsed_ms=50,
            )
            for p in platforms
        ]

        gen = ReportGenerator()
        out = gen.generate(stock_info, analysis, collect_results, self._output_dir)
        print(f" [Mock] 报告已生成: {out}")
        return out

    # ================================================================
    # Data collection
    # ================================================================

    async def _collect_quote(self, symbol: str, name: str) -> Any:
        from .collectors.quote import QuoteCollector
        from .collectors.xueqiu import XueqiuCollector

        print(" 采集实时行情...")
        xq = XueqiuCollector()
        try:
            quote = await xq.fetch_quote(symbol)
        finally:
            await xq.aclose()
        if quote and quote.pe > 0:
            info = (
                f"{quote.name}: {quote.price}"
                f" ({quote.change_pct:+.2f}%) PE={quote.pe}"
            )
            print(f"   {info} [来源: 雪球]")
            return quote
        qc = QuoteCollector()
        try:
            quote = await qc.fetch(symbol, name)
        finally:
            await qc.aclose()
        info = (
            f"{quote.name}: {quote.price}"
            f" ({quote.change_pct:+.2f}%) [来源: {quote.source}]"
        )
        print(f"   {info}")
        return quote

    async def _collect_financial(self, symbol: str) -> Any:
        from .financial.pysnowball_adapter import PysnowballAdapter
        return await PysnowballAdapter().fetch(symbol)

    async def _collect_kline(self, symbol: str) -> Any:
        from .collectors.kline import KlineCollector
        return await KlineCollector().fetch(symbol)

    async def _collect_capital_flow(self, symbol: str) -> Any:
        from .collectors.capital_flow import CapitalFlowCollector
        return await CapitalFlowCollector().fetch(symbol)

    async def _collect_research(self, symbol: str) -> Any:
        from .collectors.research_report import ResearchReportCollector
        return await ResearchReportCollector().fetch(symbol)

    async def _collect_social(self, symbol: str, name: str) -> None:
        from .collectors.social_orchestrator import SocialMediaOrchestrator

        orchestrator = SocialMediaOrchestrator()
        all_posts, collect_results = await orchestrator.collect(symbol, name)
        self._all_posts.extend(all_posts)
        self._collect_results.extend(collect_results)

    # ================================================================
    # Validation & Analysis
    # ================================================================

    @staticmethod
    def _unwrap(result, factory, *, ok, msg, fail):
        """Unwrap a gather result: check success, print status, return data."""
        data = factory()
        if not isinstance(result, Exception):
            data = result
        print(msg(data) if ok(data) else fail)
        return data

    @staticmethod
    def _validate(stock_info: StockInfo) -> tuple[list[str], dict[str, str]]:
        from .validation.integrity_checker import check_data_integrity

        warnings, confidence = check_data_integrity(stock_info)
        if warnings:
            print(" 数据质量校验:")
            for w in warnings:
                print(f"   ⚠ {w}")
        else:
            print(" 数据质量校验: 通过")
        return warnings, confidence

    async def _analyze(
        self, stock_info: StockInfo, skip_ai: bool, reports: dict | None = None,
        financial_md_path: Path | None = None,
    ) -> Any:
        use_ai = (
            self._settings.deepseek_api_key
            and not self._settings.mock_mode
            and not skip_ai
        )
        if use_ai:
            print(" DeepSeek AI 分析中...")
        elif skip_ai:
            print(" === 测试模式：跳过AI分析 ===")
        else:
            print(" AI分析跳过（未配置DEEPSEEK_API_KEY，使用基础报告）")

        return await AIAnalyzer(mock=not use_ai).analyze(
            stock_info, reports, financial_md_path
        )

    def _generate(self, stock_info: StockInfo, analysis: Any) -> Path:
        print(" 生成 HTML 报告...")
        return ReportGenerator().generate(
            stock_info, analysis, self._collect_results, self._output_dir
        )
