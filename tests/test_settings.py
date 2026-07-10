"""Tests for quality-guard switches in Settings."""
from __future__ import annotations

from aimoon.adapters.driven.config.settings import get_settings


def test_quality_switches_default():
    s = get_settings()
    assert s.direct_web_search_enabled is False
    assert s.reconcile_enabled is True
    assert s.self_check_rewrite_enabled is True
