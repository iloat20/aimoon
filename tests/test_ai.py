"""Smoke tests for AI analyzer and report generator."""

import pytest


class TestDeepSeekAIAnalyzer:
    """Test DeepSeekAIAnalyzer in mock mode."""

    @pytest.fixture
    def mock_stock_analysis(self):
        """Create a minimal StockAnalysis for testing."""
        from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
        from aimoon.core.domain.entities.capital_flow import CapitalFlowData
        from aimoon.core.domain.entities.financial import FinancialData
        from aimoon.core.domain.entities.kline import KlineData
        from aimoon.core.domain.entities.quote import StockQuote

        quote = StockQuote(
            symbol="600519",
            name="贵州茅台",
            price=1800.0,
            change=10.0,
            change_pct=0.56,
            volume=1000000,
            amount=1800000000.0,
            high=1810.0,
            low=1790.0,
            open=1795.0,
            prev_close=1790.0,
            source="sina",
        )
        financial = FinancialData(
            symbol="600519",
            report_period="2026年一季报",
            revenue=30000000000,
            net_profit=15000000000,
        )
        capital_flow = CapitalFlowData(symbol="600519")
        return StockAnalysis(
            symbol="600519",
            name="贵州茅台",
            quote=quote,
            financial=financial,
            capital_flow=capital_flow,
            social_posts=[],
            kline=KlineData(symbol="600519", bars=[]),
        )

    @pytest.mark.asyncio
    async def test_mock_analysis_returns_report(self, mock_stock_analysis):
        from aimoon.adapters.driven.ai.analyzer import DeepSeekAIAnalyzer

        analyzer = DeepSeekAIAnalyzer(mock=True)
        result = await analyzer.analyze(mock_stock_analysis)
        assert result.symbol == "600519"
        assert result.name == "贵州茅台"
        assert result.report_text is not None or result.summary is not None

    @pytest.mark.asyncio
    async def test_mock_analysis_short_summary(self, mock_stock_analysis):
        from aimoon.adapters.driven.ai.analyzer import DeepSeekAIAnalyzer

        analyzer = DeepSeekAIAnalyzer(mock=True)
        result = await analyzer.analyze(mock_stock_analysis)
        assert len(result.summary) > 0
        assert len(result.summary) <= 203  # 200 + "..."

    def test_sanitize_support_resistance_no_price(self):
        from aimoon.adapters.driven.ai.analyzer import DeepSeekAIAnalyzer
        from aimoon.core.domain.value_objects.analysis_report import AnalysisReport

        analyzer = DeepSeekAIAnalyzer(mock=True)
        report = AnalysisReport(
            symbol="600519",
            name="贵州茅台",
            summary="test",
            report_text="AI分析暂不可用，以下为基础数据汇总。",
        )
        result = analyzer._sanitize_support_resistance(report, None)
        assert result.report_text == report.report_text

    def test_sanitize_support_resistance(self):
        from aimoon.adapters.driven.ai.analyzer import DeepSeekAIAnalyzer
        from aimoon.core.domain.value_objects.analysis_report import AnalysisReport

        analyzer = DeepSeekAIAnalyzer(mock=True)
        report = AnalysisReport(
            symbol="600519",
            name="贵州茅台",
            summary="test",
            report_text="支撑位 1900.00\n阻力位 1700.00",
        )
        # support 1900 >= current 1800 -> should be overridden to 1800*0.92=1656
        # resistance 1700 <= current 1800 -> should be overridden to 1800*1.08=1944
        result = analyzer._sanitize_support_resistance(report, 1800.0)
        assert "1656" in result.report_text
        assert "1944" in result.report_text

    def test_sanitize_support_normal(self):
        from aimoon.adapters.driven.ai.analyzer import DeepSeekAIAnalyzer
        from aimoon.core.domain.value_objects.analysis_report import AnalysisReport

        analyzer = DeepSeekAIAnalyzer(mock=True)
        report = AnalysisReport(
            symbol="600519",
            name="贵州茅台",
            summary="test",
            report_text="支撑位 1500.00\n阻力位 2000.00",
        )
        # support 1500 < current 1800 (OK)
        # resistance 2000 > current 1800 (OK)
        result = analyzer._sanitize_support_resistance(report, 1800.0)
        assert result.report_text == report.report_text


class TestReportGenerator:
    """Test HTML report generation."""

    def test_generate_report(self, tmp_path):
        from aimoon.adapters.driven.report.generator import HtmlReportGenerator
        from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
        from aimoon.core.domain.entities.capital_flow import CapitalFlowData
        from aimoon.core.domain.entities.financial import FinancialData
        from aimoon.core.domain.entities.kline import KlineData
        from aimoon.core.domain.value_objects.analysis_report import AnalysisReport

        stock = StockAnalysis(
            symbol="600519",
            name="贵州茅台",
            financial=FinancialData(symbol="600519"),
            capital_flow=CapitalFlowData(symbol="600519"),
            social_posts=[],
            kline=KlineData(symbol="600519", bars=[]),
        )

        analysis = AnalysisReport(
            symbol="600519",
            name="贵州茅台",
            summary="贵州茅台基本面良好，业绩稳健增长。",
            report_text="详细分析报告内容...",
            investment_advice="本报告由AI自动生成。",
        )

        generator = HtmlReportGenerator()
        result = generator.generate(stock, analysis, [], output_dir=str(tmp_path))
        assert result.exists()
        assert result.suffix == ".html"
        content = result.read_text(encoding="utf-8")
        assert "贵州茅台" in content or "600519" in content

    def test_generate_report_empty_data(self, tmp_path):
        from aimoon.adapters.driven.report.generator import HtmlReportGenerator
        from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
        from aimoon.core.domain.entities.capital_flow import CapitalFlowData
        from aimoon.core.domain.entities.financial import FinancialData
        from aimoon.core.domain.entities.kline import KlineData
        from aimoon.core.domain.value_objects.analysis_report import AnalysisReport

        stock = StockAnalysis(
            symbol="000001",
            name="平安银行",
            financial=FinancialData(symbol="000001"),
            capital_flow=CapitalFlowData(symbol="000001"),
            social_posts=[],
            kline=KlineData(symbol="000001", bars=[]),
        )

        analysis = AnalysisReport(
            symbol="000001",
            name="平安银行",
            summary="平安银行基础数据。",
            report_text="report text",
        )

        generator = HtmlReportGenerator()
        result = generator.generate(stock, analysis, [], output_dir=str(tmp_path))
        assert result.exists()

    def test_settings_inject(self):
        """Test that inject_settings works correctly."""
        from aimoon.adapters.driven.config.settings import (
            Settings,
            get_settings,
            inject_settings,
            reset_settings,
        )

        reset_settings()
        test_settings = Settings(deepseek_api_key="test-key", mock_mode=True)
        inject_settings(test_settings)

        retrieved = get_settings()
        assert retrieved.deepseek_api_key == "test-key"
        assert retrieved.mock_mode is True

        reset_settings()
        # After reset, inject clean settings (no .env override)
        fresh = Settings(deepseek_api_key="", mock_mode=False)
        inject_settings(fresh)
        assert fresh.deepseek_api_key == ""  # Default value
