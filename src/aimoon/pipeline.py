"""Pipeline orchestrator — coordinates data collection, validation,
AI analysis, and report generation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .ai.analyzer import AIAnalyzer
from .collectors.eastmoney_playwright import GubaCollector
from .collectors.mock import mock_analysis_report, mock_social_posts, mock_stock_info
from .config.settings import get_settings
from .models.social import CollectResult
from .models.stock import FinancialReportData, StockInfo
from .report.generator import ReportGenerator
from .utils import resolve_market


class PipelineOrchestrator:
    """Coordinates the full pipeline: collect → validate → analyze → report."""

    def __init__(self, output_dir: str | None = None) -> None:
        self._settings = get_settings()
        self._output_dir = output_dir
        self._all_posts: list[Any] = []
        self._collect_results: list[CollectResult] = []

    # === Public API ===

    async def run(
        self, symbol: str, name: str, *, skip_ai: bool = False
    ) -> Path:
        """Run full pipeline with real data collection. Returns path to HTML report."""
        market = resolve_market(symbol)

        # 1. Market data
        quote = await self._collect_quote(symbol, name)
        if quote.name:
            name = quote.name

        # 2-4. Financial, K-line, capital flow, research (sequential, fast APIs)
        financial = await self._collect_financial(symbol)
        kline = await self._collect_kline(symbol)
        capital_flow = await self._collect_capital_flow(symbol)
        research = await self._collect_research(symbol)

        # 5. Social media (concurrent via CollectorRegistry)
        await self._collect_social(symbol, name)

        # 6. Financial reports from cninfo
        reports = await self._collect_reports(symbol)

        # 7. Assemble StockInfo
        stock_info = StockInfo(
            symbol=symbol,
            name=name,
            market=market,
            quote=quote,
            financial=financial,
            kline=kline,
            capital_flow=capital_flow,
            social_posts=self._all_posts,
            research=research,
            annual_report=FinancialReportData(
                **(reports.get("annual") or {})
            ),
            semi_annual_report=FinancialReportData(
                **(reports.get("semi_annual") or {})
            ),
            quarterly_report=FinancialReportData(
                **(reports.get("quarterly") or {})
            ),
        )

        # 8. Data integrity validation
        data_warnings, data_confidence = self._validate(stock_info)

        # 9. AI analysis
        analysis = await self._analyze(stock_info, skip_ai)
        analysis.data_warnings = data_warnings
        analysis.data_confidence = data_confidence

        # 10. Generate report
        return self._generate(stock_info, analysis)

    async def run_mock(self, symbol: str, name: str) -> Path:
        """Run full pipeline with mock data. Returns path to HTML report."""
        print(f" [Mock] 生成 {name}({symbol}) 的模拟分析报告...")

        stock_info = mock_stock_info(symbol)
        stock_info.name = name

        analysis = mock_analysis_report(symbol, name)

        platforms = ["雪球", "东方财富股吧", "今日头条", "微信公众号"]
        collect_results = [
            CollectResult(
                platform=p,
                status="success",
                count=len([x for x in stock_info.social_posts if x.platform == p]),
                elapsed_ms=50,
            )
            for p in platforms
        ]

        gen = ReportGenerator()
        out = gen.generate(stock_info, analysis, collect_results, self._output_dir)
        print(f" [Mock] 报告已生成: {out}")
        return out

    # === Data collection stages ===

    async def _collect_quote(self, symbol: str, name: str) -> Any:
        """Collect real-time quote: Xueqiu preferred, then QuoteCollector."""
        from .collectors.quote import QuoteCollector
        from .collectors.xueqiu import XueqiuCollector

        print(" 采集实时行情...")
        xq = XueqiuCollector()
        xq_quote = await xq.fetch_quote(symbol)
        if xq_quote and xq_quote.pe > 0:
            quote = xq_quote
            print(
                f"   {quote.name}: {quote.price}"
                f" ({quote.change_pct:+.2f}%)"
                f" PE={quote.pe} [来源: 雪球]"
            )
        else:
            qc = QuoteCollector()
            quote = await qc.fetch(symbol, name)
            print(
                f"   {quote.name}: {quote.price}"
                f" ({quote.change_pct:+.2f}%)"
                f" [来源: {quote.source}]"
            )
        return quote

    async def _collect_financial(self, symbol: str) -> Any:
        """Collect financial data via pysnowball."""
        from .financial.pysnowball_adapter import PysnowballAdapter

        print(" 采集财务数据...")
        adapter = PysnowballAdapter()
        financial = await adapter.fetch(symbol)
        if financial.report_period:
            print(
                f"   报告期: {financial.report_period} | ROE: {financial.roe}%"
                f" [来源: {financial.source}]"
            )
        else:
            print("   财报获取失败，使用空数据。可配置 XUEQIU_TOKEN 启用。")
        return financial

    async def _collect_kline(self, symbol: str) -> Any:
        """Collect K-line history."""
        from .collectors.kline import KlineCollector

        print(" 采集K线历史...")
        kline = await KlineCollector().fetch(symbol)
        if kline.bars:
            print(f"   K线: {len(kline.bars)}根 [{kline.source}]")
        else:
            print("   K线获取失败，技术分析将使用基础数据。")
        return kline

    async def _collect_capital_flow(self, symbol: str) -> Any:
        """Collect capital flow data."""
        from .collectors.fund_flow import FundFlowCollector

        print(" 采集资金流向...")
        capital_flow = await FundFlowCollector().fetch(symbol)
        if capital_flow.source != "all_failed":
            print(
                f"   主力5日: {capital_flow.main_net_5d / 1e8:.2f}亿"
                f" [{capital_flow.source}]"
            )
        else:
            print("   资金面数据获取失败。")
        return capital_flow

    async def _collect_research(self, symbol: str) -> Any:
        """Collect institutional research reports."""
        from .collectors.research_report import ResearchReportCollector

        print(" 采集机构研报...")
        research = await ResearchReportCollector().fetch(symbol)
        if research.total_count > 0:
            print(f"   研报: {research.total_count}条 [来源: {research.source}]")
        else:
            print("   研报数据获取失败。")
        return research

    async def _collect_social(self, symbol: str, name: str) -> None:
        """Collect social media sentiment from multiple platforms."""
        print(" 采集社交媒体舆情...")

        # 东方财富股吧 (unified collector with internal fallback chain)
        guba = GubaCollector()
        guba_result = await guba.collect(symbol, name)
        if guba_result.status == "success" and guba_result.count > 0:
            self._all_posts.extend(guba_result.posts)
            source_tag = guba_result.error or "股吧"
            print(f"   东方财富股吧: {guba_result.count}条 [{source_tag}]")
        else:
            mock = mock_social_posts("东方财富股吧", symbol, name)
            self._all_posts.extend(mock)
            print(f"   东方财富股吧: {len(mock)}条 (mock)")
            guba_result = CollectResult(
                platform="东方财富股吧",
                status="success (mock)",
                count=len(mock),
                elapsed_ms=100,
            )
        self._collect_results.append(guba_result)

        # 巨潮资讯公告
        print(" 采集巨潮资讯公告...")
        from .collectors.cninfo import CninfoCollector

        cninfo = CninfoCollector()
        cninfo_result = await cninfo.collect(symbol, name)
        if cninfo_result.status == "success" and cninfo_result.count > 0:
            self._all_posts.extend(cninfo_result.posts)
            print(f"   巨潮资讯: {cninfo_result.count}条 [真实数据]")
        else:
            print(f"   巨潮资讯: 0条 ({cninfo_result.error})")
        self._collect_results.append(cninfo_result)

        # Other platforms with mock fallback
        for p_name, module_path, cls_name in [
            ("今日头条", ".collectors.toutiao", "ToutiaoCollector"),
            ("微信公众号", ".collectors.wechat", "WechatCollector"),
        ]:
            try:
                import importlib

                mod = importlib.import_module(module_path, "aimoon")
                cls = getattr(mod, cls_name)
                collector = cls()
                result = await collector.collect(symbol, name)
                if result.status == "success" and result.count > 0:
                    self._all_posts.extend(result.posts)
                    print(f"   {p_name}: {result.count}条 [真实数据]")
                elif result.status == "skipped":
                    mock = mock_social_posts(p_name, symbol, name)
                    self._all_posts.extend(mock)
                    result = CollectResult(
                        platform=p_name,
                        status="success (mock)",
                        count=len(mock),
                        elapsed_ms=100,
                    )
                    print(f"   {p_name}: {len(mock)}条 (mock) [{result.error}]")
                else:
                    mock = mock_social_posts(p_name, symbol, name)
                    self._all_posts.extend(mock)
                    result = CollectResult(
                        platform=p_name,
                        status="success (mock)",
                        count=len(mock),
                        elapsed_ms=100,
                    )
                    print(f"   {p_name}: {len(mock)}条 (mock)")
                self._collect_results.append(result)
            except Exception:
                mock = mock_social_posts(p_name, symbol, name)
                self._all_posts.extend(mock)
                self._collect_results.append(
                    CollectResult(
                        platform=p_name,
                        status="success (mock)",
                        count=len(mock),
                        elapsed_ms=100,
                    )
                )
                print(f"   {p_name}: {len(mock)}条 (mock)")

    async def _collect_reports(self, symbol: str) -> dict:
        """Fetch latest financial reports from cninfo."""
        print(" 获取最新财务报告...")
        from .financial.annual_report import fetch_reports

        return await fetch_reports(symbol)

    # === Validation & Analysis ===

    @staticmethod
    def _validate(stock_info: StockInfo) -> tuple[list[str], dict[str, str]]:
        """Run data integrity checks."""
        from .validation.integrity_checker import check_data_integrity

        warnings, confidence = check_data_integrity(stock_info)
        if warnings:
            print(" 数据质量校验:")
            for w in warnings:
                print(f"   ⚠ {w}")
        else:
            print(" 数据质量校验: 通过")
        return warnings, confidence

    async def _analyze(self, stock_info: StockInfo, skip_ai: bool) -> Any:
        """Run AI analysis."""
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

        analyzer = AIAnalyzer(mock=not use_ai)
        return await analyzer.analyze(stock_info)

    def _generate(self, stock_info: StockInfo, analysis: Any) -> Path:
        """Generate HTML report."""
        print(" 生成 HTML 报告...")
        gen = ReportGenerator()
        return gen.generate(
            stock_info, analysis, self._collect_results, self._output_dir
        )
