"""Tests for CLI"""
from unittest.mock import patch


class TestParseArgs:
    def test_default_args(self) -> None:
        with patch("sys.argv", ["aimoon"]):
            from aimoon.cli import parse_args
            args = parse_args()
            assert args.top == 20
            assert args.workers == 20

    def test_demo_flag(self) -> None:
        with patch("sys.argv", ["aimoon", "--demo"]):
            from aimoon.cli import parse_args
            assert parse_args().demo is True

    def test_backtest_subcommand(self) -> None:
        with patch("sys.argv", ["aimoon", "backtest", "--hold-days", "10"]):
            from aimoon.cli import parse_args
            args = parse_args()
            assert args.command == "backtest"
            assert args.hold_days == 10

    def test_cache_clear(self) -> None:
        with patch("sys.argv", ["aimoon", "cache", "clear"]):
            from aimoon.cli import parse_args
            args = parse_args()
            assert args.command == "cache"
            assert args.cache_action == "clear"
