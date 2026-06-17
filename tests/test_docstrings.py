"""Tests for key function docstrings."""

from __future__ import annotations

import pathlib


class TestDocstringsPresent:
    """Verify that key functions have docstrings."""

    def should_have_docstring_on_score_stock(self) -> None:
        """_score_stock should have a docstring."""
        src = pathlib.Path("src/aimoon/enhanced_backtest/engine.py").read_text(encoding="utf-8")
        idx = src.find("def _score_stock(")
        assert idx != -1
        after = src[idx:idx + 500]
        assert chr(34) * 3 in after, "_score_stock should have a docstring"
