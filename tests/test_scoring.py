"""Tests for scoring functions and constants."""

import pytest


class TestScoringConstants:
    """Verify scoring constants are correctly defined."""

    def test_weights_sum_to_one(self):
        from src.aimoon.scoring.constants import (
            WEIGHT_CAPITAL_FLOW,
            WEIGHT_FUNDAMENTAL,
            WEIGHT_NEWS,
            WEIGHT_SENTIMENT,
            WEIGHT_TECHNICAL,
        )

        total = (
            WEIGHT_SENTIMENT
            + WEIGHT_TECHNICAL
            + WEIGHT_FUNDAMENTAL
            + WEIGHT_CAPITAL_FLOW
            + WEIGHT_NEWS
        )
        assert total == 1.0, f"Weights sum to {total}, expected 1.0"

    def test_sentiment_thresholds_sorted_descending(self):
        from src.aimoon.scoring.constants import SENTIMENT_THRESHOLDS

        prev = 2.0
        for threshold, _ in SENTIMENT_THRESHOLDS:
            assert threshold < prev, f"Thresholds must be descending: got {threshold} after {prev}"
            prev = threshold

    def test_sentiment_thresholds_in_range(self):
        from src.aimoon.scoring.constants import MAX_SCORE, MIN_SCORE, SENTIMENT_THRESHOLDS

        for _, score in SENTIMENT_THRESHOLDS:
            assert MIN_SCORE <= score <= MAX_SCORE


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
        from src.aimoon.indicators.capital_flow import capital_flow_score

        score, detail, force = capital_flow_score(neutral_flow)
        assert score == 3
        assert force == "持平"

    def test_strong_inflow(self, strong_inflow):
        from src.aimoon.indicators.capital_flow import capital_flow_score

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

    def test_classify_sentiment_positive(self):
        from src.aimoon.utils import classify_sentiment

        assert classify_sentiment("主力资金大幅买入，业绩大增") == "positive"
        assert classify_sentiment("这是个大利好") == "positive"

    def test_classify_sentiment_negative(self):
        from src.aimoon.utils import classify_sentiment

        assert classify_sentiment("暴跌出逃，踩踏了") == "negative"
        assert classify_sentiment("利空来袭，退市风险") == "negative"

    def test_classify_sentiment_neutral(self):
        from src.aimoon.utils import classify_sentiment

        assert classify_sentiment("今日大盘震荡") == "neutral"
        assert classify_sentiment("") == "neutral"

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
