"""Smoke tests for AI analyzer and report generator."""

import pytest

from aimoon.adapters.driven.ai.post_processor import sanitize_support_resistance


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
        from aimoon.core.domain.value_objects.analysis_report import AnalysisReport

        report = AnalysisReport(
            symbol="600519",
            name="贵州茅台",
            summary="test",
            report_text="AI分析暂不可用，以下为基础数据汇总。",
        )
        result = sanitize_support_resistance(report, None)
        assert result.report_text == report.report_text

    def test_sanitize_support_resistance(self):
        from aimoon.core.domain.value_objects.analysis_report import AnalysisReport

        report = AnalysisReport(
            symbol="600519",
            name="贵州茅台",
            summary="test",
            report_text="支撑位 1900.00\n阻力位 1700.00",
        )
        # support 1900 >= current 1800 -> should be overridden to 1800*0.92=1656
        # resistance 1700 <= current 1800 -> should be overridden to 1800*1.08=1944
        result = sanitize_support_resistance(report, 1800.0)
        assert "1656" in result.report_text
        assert "1944" in result.report_text

    def test_sanitize_support_normal(self):
        from aimoon.core.domain.value_objects.analysis_report import AnalysisReport

        report = AnalysisReport(
            symbol="600519",
            name="贵州茅台",
            summary="test",
            report_text="支撑位 1500.00\n阻力位 2000.00",
        )
        # support 1500 < current 1800 (OK)
        # resistance 2000 > current 1800 (OK)
        result = sanitize_support_resistance(report, 1800.0)
        assert result.report_text == report.report_text


def test_sanitize_numeric_artifacts_revenue_to_yi():
    """营收类灾难 token(元单位 + %)应换算成「亿」回填,不留悬空占位符。"""
    from aimoon.adapters.driven.ai.post_processor import sanitize_numeric_artifacts

    cases = [
        # 科学计数法元单位:1.7e11 = 1700亿(≥100亿取整)
        ("空调业务占营收 1.7e11%", "空调业务占营收约 1700亿"),
        # 9 位以上整数(含千分位逗号):171,118,000,000 = 1711.18亿 → 取整 1711亿
        ("主营业务营收为 171,118,000,000%", "主营业务营收为约 1711亿"),
        # 连接词「营收」直接连数值
        ("其中营收 2.01e11%", "其中营收约 2010亿"),
        # 连接词「占比」:5.55e10 = 555亿
        ("该板块占比 5.55e10%", "该板块占比约 555亿"),
    ]
    for src, expected in cases:
        got = sanitize_numeric_artifacts(src)
        assert expected in got, f"{src!r} -> {got!r} (期望含 {expected!r})"
        # 不再残留悬空占位符
        assert "见近年财务时序表" not in got


def test_sanitize_numeric_artifacts_no_dangling_placeholder():
    """整篇正文的灾难 token 清洗后,不应再出现悬空占位符。"""
    from aimoon.adapters.driven.ai.post_processor import sanitize_numeric_artifacts

    md = (
        "公司主业稳健,占营收 1.7e11%。\n"
        "毛利率稳定,营收端为 99999999999%。\n"
        "其余科目正常。"
    )
    got = sanitize_numeric_artifacts(md)
    assert "见近年财务时序表" not in got
    assert "占营收约" in got
    assert "营收约" in got


def test_sanitize_numeric_artifacts_collapse_stutter():
    """模型把同一营收灾难 token 连写多遍,清洗后应折叠为首个,消除口吃。"""
    from aimoon.adapters.driven.ai.post_processor import sanitize_numeric_artifacts

    src = "经销商出走,营收 171118000000%,营收为 171118000000%,营收为 171118000000% 连续两年。"
    got = sanitize_numeric_artifacts(src)
    # 只保留一个营收片段,不再三连
    assert got.count("1711亿") == 1, got
    assert "，营收为约" not in got and ",营收为约" not in got
    assert "连续两年" in got


def test_fix_net_cash_pe_corrects_pe_confusion():
    """正文把净现金调整 PE 误写成 PE(TTM) 值时,应按附录权威表纠正。"""
    from aimoon.adapters.driven.ai.post_processor import _fix_net_cash_pe

    appendix = (
        "| 当前 PE(TTM) | 7.79 | 行情派生 |\n"
        "| 净现金调整 PE | 4.04 | (市值−货币资金)/净利润 |\n"
    )
    # 正序:误写 7.79 → 纠正为 4.04
    body = "格力以净现金调整 PE 7.79倍交易,剔除现金后仍便宜。"
    assert "净现金调整 PE 4.04倍" in _fix_net_cash_pe(body, appendix)
    # 反序:7.79倍净现金调整 PE → 4.04倍净现金调整 PE
    body2 = "以 7.79 倍PE、7.79倍净现金调整 PE 交易"
    assert "4.04倍净现金调整 PE" in _fix_net_cash_pe(body2, appendix)
    # 正确值不动
    body3 = "净现金调整 PE 4.04(见估值表)"
    assert "净现金调整 PE 4.04" in _fix_net_cash_pe(body3, appendix)
    # 非混淆数值(如压力情景另述)不误改:此处 PE 后紧跟文字而非数字
    body4 = "压力情景下净现金调整 PE 被动升至 15 倍以上"
    assert "15 倍" in _fix_net_cash_pe(body4, appendix)


def test_fix_capex_corrects_pe_confusion():
    """正文把资本开支 Capex 误写成 PE(TTM) 值时,应按附录权威表纠正(P1 #13)。"""
    from aimoon.adapters.driven.ai.post_processor import _fix_capex

    # 附录:财务健康扩展表给权威 Capex,估值安全边际表给 PE(TTM)
    appendix = (
        "| 资本开支 Capex | 17.2 亿 | 购建固定资产(真实 capex) |\n"
        "| 当前 PE(TTM) | 7.79 | 行情派生 |\n"
    )
    # 误写 7.79(=PE) → 纠正为权威 17.2
    body = "资本开支 Capex 7.79 亿，占营收约 1711亿（见财务健康扩"
    assert "资本开支 Capex 17.2 亿" in _fix_capex(body, appendix)
    # 英文前缀 capex(大小写不敏感)同样纠正
    body_l = "capex 7.79亿,远低于同行"
    assert "capex 17.2 亿" in _fix_capex(body_l, appendix)
    # 亿元连写
    body_yuan = "资本开支 Capex 7.79亿元,自由现金流充裕"
    assert "资本开支 Capex 17.2 亿元" in _fix_capex(body_yuan, appendix)
    # 正确值不动(已是权威 17.2)
    body_ok = "资本开支 Capex 17.2 亿,真实 capex 可控"
    assert _fix_capex(body_ok, appendix) == body_ok
    # 附录缺 PE(TTM) → 无从判别,不动
    appendix_no_pe = "| 资本开支 Capex | 17.2 亿 | 购建固定资产 |\n"
    body2 = "资本开支 Capex 7.79 亿"
    assert _fix_capex(body2, appendix_no_pe) == body2


# ---- Task 11: use_pipeline_v2 routing ----


def _make_si() -> object:
    from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis

    return StockAnalysis(symbol="600519", name="贵州茅台")


@pytest.mark.asyncio
async def test_analyze_routes_to_legacy_when_flag_false():
    from unittest.mock import AsyncMock, patch

    from aimoon.adapters.driven.ai.analyzer import DeepSeekAIAnalyzer
    from aimoon.core.domain.value_objects.analysis_report import AnalysisReport

    leg = AnalysisReport(
        symbol="600519", name="贵州茅台", summary="leg", report_text="leg", investment_advice="x"
    )
    with patch.object(DeepSeekAIAnalyzer, "_legacy_analyze", new_callable=AsyncMock) as m:
        m.return_value = leg
        az = DeepSeekAIAnalyzer(mock=True)
        out = await az.analyze(_make_si(), use_pipeline_v2=False)
        m.assert_called_once()
        assert out.report_text == "leg"


@pytest.mark.asyncio
async def test_analyze_routes_to_pipeline_when_flag_true():
    from unittest.mock import AsyncMock, patch

    from aimoon.adapters.driven.ai.analyzer import DeepSeekAIAnalyzer
    from aimoon.core.domain.value_objects.analysis_report import AnalysisReport

    v2 = AnalysisReport(
        symbol="600519", name="贵州茅台", summary="v2", report_text="v2", investment_advice="x"
    )
    with (
        patch.object(DeepSeekAIAnalyzer, "_legacy_analyze", new_callable=AsyncMock),
        patch.object(DeepSeekAIAnalyzer, "_pipeline_analyze", new_callable=AsyncMock) as m2,
    ):
        m2.return_value = v2
        az = DeepSeekAIAnalyzer(mock=True)
        out = await az.analyze(_make_si(), use_pipeline_v2=True)
        m2.assert_called_once()
        assert out.report_text == "v2"


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
