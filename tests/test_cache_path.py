"""Tests for cache path using project-relative location."""

from __future__ import annotations

import pathlib


class TestCachePathRelative:
    """Verify that ML cache path is project-relative."""

    def should_use_project_relative_cache_dir(self) -> None:
        """ensemble.py _DEFAULT_CACHE_DIR should use relative path."""
        src = pathlib.Path("src/aimoon/ml/ensemble.py").read_text(encoding="utf-8")
        assert 'Path(".aimoon_cache")' in src
