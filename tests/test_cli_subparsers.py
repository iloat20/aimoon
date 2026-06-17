"""Tests for CLI argument parsing."""

from __future__ import annotations

import subprocess
import sys


class TestCLISubparsers:
    """Tests for CLI subparser required flag."""

    def should_resolve_backtest_help_fast(self) -> None:
        """Running aimoon backtest --help should exit cleanly and fast."""
        result = subprocess.run(
            [sys.executable, "-m", "aimoon.cli", "backtest", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "backtest" in result.stdout

    def should_fail_when_cache_given_without_subcommand(self) -> None:
        """Running aimoon cache without subcommand should fail."""
        result = subprocess.run(
            [sys.executable, "-m", "aimoon.cli", "cache"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0

    def should_fail_when_watchlist_given_without_subcommand(self) -> None:
        """Running aimoon watchlist without subcommand should fail."""
        result = subprocess.run(
            [sys.executable, "-m", "aimoon.cli", "watchlist"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
