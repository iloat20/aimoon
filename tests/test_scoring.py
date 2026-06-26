"""Tests for scoring functions and constants."""

import pytest


class TestScoringConstants:
    """Verify scoring constants are correctly defined."""

    def test_basic_constants_exist(self):
        from src.aimoon.scoring.constants import (
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


class TestCapitalFlowScore:
    """Test capital_flow_score with various scenarios."""

    @pytest.fixture
    def neutral_flow(self):
        pytest.importorskip("pydantic")
        from src.aimoon.models.stock import CapitalFlowData

        return CapitalFlowData(symbol="000001")

    @pytest.fixture
    def strong_inflow(self):
        pytest.importorskip("pydantic")
        from src.aimoon.models.stock import CapitalFlowData

        return CapitalFlowData(
            symbol="000001",
            main_net_5d=6e8,
            net_3d=2e8,
            net_10d=3e8,
            net_20d=4e8,
            northbound_chg=1.5e8,
        )

    def test_neutral_flow(self, neutral_flow):
        from src.aimoon.scoring import capital_flow_score

        score, detail, force = capital_flow_score(neutral_flow)
        assert score == 3
        assert force == "持平"

    def test_strong_inflow(self, strong_inflow):
        from src.aimoon.scoring import capital_flow_score

        score, detail, force = capital_flow_score(strong_inflow)
        assert score == 5
        assert force == "流入"


class TestUtils:
    """Test utility functions."""

    def test_parse_chinese_count(self):
        from src.aimoon.utils import parse_chinese_count

        assert parse_chinese_count("1.2万") == 12000
        assert parse_chinese_count("3.5亿") == 350_000_000
        assert parse_chinese_count("123") == 123
        assert parse_chinese_count("") == 0
        assert parse_chinese_count("abc") == 0

    def test_resolve_market(self):
        from src.aimoon.utils import resolve_market

        assert resolve_market("600519") == "SH"
        assert resolve_market("000001") == "SZ"
        assert resolve_market("300750") == "SZ"
        assert resolve_market("430047") == "BJ"
        assert resolve_market("830799") == "BJ"

    def test_resolve_symbol(self):
        from src.aimoon.utils import resolve_symbol

        sym, market, name = resolve_symbol("600519")
        assert sym == "600519"
        assert market == "SH"

        sym, market, name = resolve_symbol("1")
        assert sym == "000001"
        assert market == "SZ"

    def test_extract_toutiao_url(self):
        from src.aimoon.utils import extract_toutiao_url

        result = extract_toutiao_url(
            "https://so.toutiao.com/jump?url=group%252F1234567890123456"
        )
        assert "toutiao.com/article/1234567890123456" in result

    def test_extract_toutiao_url_empty(self):
        from src.aimoon.utils import extract_toutiao_url

        assert extract_toutiao_url("") == ""
        assert extract_toutiao_url("https://example.com") == ""
