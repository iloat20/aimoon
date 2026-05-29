"""Tests for output formatter"""
import os
import pytest
from unittest.mock import patch
from dataclasses import replace

from aimoon.config import Config
from aimoon.models import Signal, ScoredStock
from aimoon.output import OutputFormatter


@pytest.fixture
def formatter() -> OutputFormatter:
    return OutputFormatter(Config())


@pytest.fixture
def sample_results() -> list[ScoredStock]:
    return [
        ScoredStock(
            code="000001", name="Test1", price=10.0,
            pct_change=2.5, turnover=5.0,
            signals=(Signal("ma_golden", "MA金叉", 2), Signal("rsi_strong", "RSI强势", 2)),
        ),
        ScoredStock(
            code="600519", name="Test2", price=200.0,
            pct_change=-1.0, turnover=3.0,
            signals=(Signal("macd_death", "MACD死叉", -2),),
        ),
    ]


class TestOutputFormatter:
    def test_display_no_crash(self, formatter, sample_results) -> None:
        formatter.display(sample_results)

    def test_display_empty(self, formatter) -> None:
        formatter.display([])

    def test_export_csv(self, formatter, sample_results, tmp_path) -> None:
        test_cfg = replace(formatter.cfg, output_dir=str(tmp_path))
        with patch.object(formatter, "cfg", test_cfg):
            filepath = formatter.export_csv(sample_results, filename="test.csv")
            assert os.path.exists(filepath)
            assert filepath.endswith(".csv")

    def test_export_markdown(self, formatter, sample_results, tmp_path) -> None:
        test_cfg = replace(formatter.cfg, output_dir=str(tmp_path))
        with patch.object(formatter, "cfg", test_cfg):
            filepath = formatter.export_markdown(sample_results, filename="test.md")
            assert os.path.exists(filepath)
            with open(filepath, encoding="utf-8") as f:
                content = f.read()
            assert "000001" in content
