"""Tests for output formatter"""
from __future__ import annotations

import os

import pytest

from aimoon.output.formatter import OutputFormatter
from aimoon.strategies.screener import SignalScore


@pytest.fixture
def formatter() -> OutputFormatter:
    return OutputFormatter()


@pytest.fixture
def sample_results() -> list[SignalScore]:
    return [
        SignalScore(
            stock_code="000001", stock_name="Test1", price=10.0,
            pct_change=2.5, turnover=5.0, total_score=6,
            suggestion="强烈买入", confidence="高", signals=["MA金叉", "RSI超卖"],
        ),
        SignalScore(
            stock_code="600519", stock_name="Test2", price=200.0,
            pct_change=-1.0, turnover=3.0, total_score=-3,
            suggestion="建议卖出", confidence="中高", signals=["MACD死叉"],
        ),
    ]


class TestOutputFormatter:
    def test_display_results_no_crash(self, formatter: OutputFormatter, sample_results) -> None:
        formatter.display_results(sample_results)

    def test_display_empty_results(self, formatter: OutputFormatter) -> None:
        formatter.display_results([])

    def test_export_csv(self, formatter: OutputFormatter, sample_results, tmp_path) -> None:
        filepath = formatter.export_csv(sample_results, filename="test.csv")
        assert os.path.exists(filepath)
        assert filepath.endswith("test.csv")
        os.remove(filepath)

    def test_chinese_style_buy(self) -> None:
        r = SignalScore(
            stock_code="000001", stock_name="T", price=10.0,
            pct_change=1.0, turnover=5.0, suggestion="买入",
        )
        assert "买" in r.suggestion

    def test_chinese_style_sell(self) -> None:
        r = SignalScore(
            stock_code="000001", stock_name="T", price=10.0,
            pct_change=1.0, turnover=5.0, suggestion="强烈卖出",
        )
        assert "卖" in r.suggestion

    def test_chinese_style_hold(self) -> None:
        r = SignalScore(
            stock_code="000001", stock_name="T", price=10.0,
            pct_change=1.0, turnover=5.0, suggestion="观望",
        )
        assert "买" not in r.suggestion and "卖" not in r.suggestion
