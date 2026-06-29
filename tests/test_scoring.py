"""Tests for scoring functions and constants."""

import pytest


class TestScoringConstants:
    """Verify scoring constants are correctly defined."""

    def test_basic_constants_exist(self):
        from aimoon.core.domain.services import (
            CAPITAL_FLOW_STRONG_IN,
            DEFAULT_SCORE,
            FUND_ROE_EXCELLENT,
            MAX_SCORE,
            MIN_SCORE,
            NEWS_BUY_RATIO_BULLISH,
            WEIGHT_CAPITAL_FLOW,
            WEIGHT_FUNDAMENTAL,
            WEIGHT_NEWS,
        )

        assert WEIGHT_FUNDAMENTAL > 0
        assert WEIGHT_CAPITAL_FLOW > 0
        assert WEIGHT_NEWS > 0
        assert DEFAULT_SCORE == 3
        assert MIN_SCORE == 1
        assert MAX_SCORE == 5
        assert FUND_ROE_EXCELLENT > 0
        assert NEWS_BUY_RATIO_BULLISH > 0
        assert CAPITAL_FLOW_STRONG_IN > 0


class TestFundamentalScore:
    """Test fundamental_score with various financial scenarios."""

    @pytest.fixture
    def base_financial(self):
        pytest.importorskip("pydantic")
        from aimoon.core.domain import FinancialData

        return FinancialData(symbol="000001", report_period="2026一季报")

    def test_empty_financial(self):
        from aimoon.core.domain.services import fundamental_score

        score, detail = fundamental_score({})  # type: ignore[arg-type]
        assert score == 3

    def test_roe_zero(self, base_financial):
        from aimoon.core.domain.services import fundamental_score

        base_financial.roe = 0
        score, detail = fundamental_score(base_financial)
        assert score == 2
        assert "盈亏平衡" in detail

    def test_roe_negative(self, base_financial):
        from aimoon.core.domain.services import fundamental_score

        base_financial.roe = -5.0
        score, detail = fundamental_score(base_financial)
        assert score == 1
        assert "亏损" in detail

    def test_roe_excellent_good_revenue(self, base_financial):
        from aimoon.core.domain.services import fundamental_score

        base_financial.roe = 20.0
        base_financial.revenue_yoy = 15.0
        base_financial.net_profit_yoy = 15.0
        score, detail = fundamental_score(base_financial)
        assert score == 5

    def test_roe_ok_revenue_bad(self, base_financial):
        from aimoon.core.domain.services import fundamental_score

        base_financial.roe = 10.0
        base_financial.revenue_yoy = -10.0
        base_financial.net_profit_yoy = -15.0
        score, detail = fundamental_score(base_financial)
        assert score == 1  # 3 - 0 (ROE介于 8-15 无调整) - 1(营收) - 1(利润)


class TestNewsScore:
    """Test news_score with research report buy ratios."""

    @pytest.fixture
    def base_research(self):
        pytest.importorskip("pydantic")
        from aimoon.core.domain import ResearchReportData

        return ResearchReportData(symbol="000001")

    def test_empty_research(self):
        from aimoon.core.domain.services import news_score

        score, detail = news_score({})  # type: ignore[arg-type]
        assert score == 3

    def test_no_reports(self, base_research):
        from aimoon.core.domain.services import news_score

        score, detail = news_score(base_research)
        assert score == 3

    def test_buy_ratio_excellent(self, base_research):
        from aimoon.core.domain import ResearchReport, ResearchReportData
        from aimoon.core.domain.services import news_score

        reports = []
        for _ in range(8):
            reports.append(ResearchReport(rating="买入"))
        for _ in range(1):
            reports.append(ResearchReport(rating="增持"))
        for _ in range(1):
            reports.append(ResearchReport(rating="中性"))
        research = ResearchReportData(symbol="000001", reports=reports)
        score, detail = news_score(research)
        assert score == 5  # buy_ratio=0.9 >= 0.8

    def test_buy_ratio_good(self, base_research):
        from aimoon.core.domain import ResearchReport, ResearchReportData
        from aimoon.core.domain.services import news_score

        reports = []
        for _ in range(6):
            reports.append(ResearchReport(rating="买入"))
        for _ in range(1):
            reports.append(ResearchReport(rating="增持"))
        for _ in range(3):
            reports.append(ResearchReport(rating="中性"))
        research = ResearchReportData(symbol="000001", reports=reports)
        score, detail = news_score(research)
        assert score == 4  # buy_ratio=0.7 >= 0.6

    def test_buy_ratio_bearish(self, base_research):
        from aimoon.core.domain import ResearchReport, ResearchReportData
        from aimoon.core.domain.services import news_score

        reports = []
        for _ in range(1):
            reports.append(ResearchReport(rating="买入"))
        for _ in range(9):
            reports.append(ResearchReport(rating="中性"))
        research = ResearchReportData(symbol="000001", reports=reports)
        score, detail = news_score(research)
        assert score == 2  # buy_ratio=0.1 ≤ 0.2 → score=2

    def test_buy_ratio_very_bearish(self, base_research):
        from aimoon.core.domain import ResearchReport, ResearchReportData
        from aimoon.core.domain.services import news_score

        reports = []
        for _ in range(2):
            reports.append(ResearchReport(rating="买入"))
        for _ in range(98):
            reports.append(ResearchReport(rating="中性"))
        research = ResearchReportData(symbol="000001", reports=reports)
        score, detail = news_score(research)
        assert score == 1  # buy_ratio=0.02 ≤ 0.05 → score=1


class TestCapitalFlowScore:
    """Test capital_flow_score with various scenarios."""

    @pytest.fixture
    def neutral_flow(self):
        pytest.importorskip("pydantic")
        from aimoon.core.domain import CapitalFlowData

        return CapitalFlowData(symbol="000001")

    @pytest.fixture
    def strong_inflow(self):
        pytest.importorskip("pydantic")
        from aimoon.core.domain import CapitalFlowData

        return CapitalFlowData(
            symbol="000001",
            main_net_5d=6e8,
            main_net_3d=2e8,
            main_net_10d=3e8,
            main_net_20d=4e8,
            northbound_chg=1.5e8,
        )

    def test_neutral_flow(self, neutral_flow):
        from aimoon.core.domain.services import capital_flow_score

        score, detail, force = capital_flow_score(neutral_flow)
        assert score == 3
        assert force == "持平"

    def test_strong_inflow(self, strong_inflow):
        from aimoon.core.domain.services import capital_flow_score

        score, detail, force = capital_flow_score(strong_inflow)
        assert score == 5
        assert force == "流入"

    def test_strong_outflow(self):
        pytest.importorskip("pydantic")
        from aimoon.core.domain import CapitalFlowData
        from aimoon.core.domain.services import capital_flow_score

        cf = CapitalFlowData(
            symbol="000001",
            main_net_5d=-6e8,
            main_net_3d=-2e8,
            main_net_10d=-3e8,
            main_net_20d=-4e8,
            northbound_chg=-1.5e8,
        )
        score, detail, force = capital_flow_score(cf)
        assert score <= 2, f"极端流出的预期分数≤2，实际得到{score}"
        assert force == "流出"


class TestUtils:
    """Test utility functions."""

    def test_parse_chinese_count(self):
        from aimoon.adapters.driven.common.parsers import parse_chinese_count

        assert parse_chinese_count("1.2万") == 12000
        assert parse_chinese_count("3.5亿") == 350_000_000
        assert parse_chinese_count("123") == 123
        assert parse_chinese_count("") == 0
        assert parse_chinese_count("abc") == 0

    def test_resolve_market(self):
        from aimoon.core.domain.services import resolve_market

        assert resolve_market("600519") == "SH"
        assert resolve_market("000001") == "SZ"
        assert resolve_market("300750") == "SZ"
        assert resolve_market("430047") == "BJ"
        assert resolve_market("830799") == "BJ"

    def test_resolve_symbol(self):
        from aimoon.core.domain.services import resolve_symbol

        sym, market, name = resolve_symbol("600519")
        assert sym == "600519"
        assert market == "SH"

        sym, market, name = resolve_symbol("1")
        assert sym == "000001"
        assert market == "SZ"

    def test_extract_toutiao_url(self):
        from aimoon.adapters.driven.common.parsers import extract_toutiao_url

        result = extract_toutiao_url("https://so.toutiao.com/jump?url=group%252F1234567890123456")
        assert "toutiao.com/article/1234567890123456" in result

    def test_extract_toutiao_url_empty(self):
        from aimoon.adapters.driven.common.parsers import extract_toutiao_url

        assert extract_toutiao_url("") == ""
        assert extract_toutiao_url("https://example.com") == ""
