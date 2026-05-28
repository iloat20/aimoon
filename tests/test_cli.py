"""Tests for CLI"""
from __future__ import annotations

import pytest
from unittest.mock import patch


class TestParseArgs:
    def test_default_args(self) -> None:
        with patch("sys.argv", ["aimoon"]):
            from aimoon.cli import parse_args
            args = parse_args()
            assert args.top == 30
            assert args.workers == 5
            assert args.demo is False

    def test_demo_flag(self) -> None:
        with patch("sys.argv", ["aimoon", "--demo"]):
            from aimoon.cli import parse_args
            args = parse_args()
            assert args.demo is True

    def test_cache_clear_subcommand(self) -> None:
        with patch("sys.argv", ["aimoon", "cache", "clear"]):
            from aimoon.cli import parse_args
            args = parse_args()
            assert args.command == "cache"
            assert args.cache_action == "clear"

    def test_backtest_subcommand(self) -> None:
        with patch("sys.argv", ["aimoon", "backtest", "--hold-days", "10"]):
            from aimoon.cli import parse_args
            args = parse_args()
            assert args.command == "backtest"
            assert args.hold_days == 10


class TestGenerateDemo:
    def test_generate_demo_returns_data(self) -> None:
        from aimoon.cli import generate_demo
        spot_df, klines = generate_demo()
        assert len(spot_df) == 30
        assert len(klines) == 30
        assert "000001" in klines
        assert len(klines["000001"]) == 120
