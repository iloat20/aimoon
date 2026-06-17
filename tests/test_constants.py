"""Tests for magic number extraction in enhanced_backtest.py."""

from __future__ import annotations

import pathlib


class TestConstantsExtracted:
    """Verify that magic numbers have been extracted to named constants."""

    def should_have_min_kline_length_constant(self) -> None:
        """enhanced_backtest.py should define _MIN_KLINE_LENGTH constant."""
        src = pathlib.Path("src/aimoon/enhanced_backtest/helpers.py").read_text(encoding="utf-8")
        assert "MIN_KLINE_LENGTH" in src

    def should_have_roc_threshold_constants(self) -> None:
        """enhanced_backtest.py should define ROC threshold constants."""
        src = pathlib.Path("src/aimoon/enhanced_backtest/helpers.py").read_text(encoding="utf-8")
        assert "ROC5_DROP_THRESHOLD" in src
        assert "ROC5_MODERATE_DROP" in src
        assert "ROC5_RISE_THRESHOLD" in src
