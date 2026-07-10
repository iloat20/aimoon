"""Unit tests for the DeepSeek thinking-mode request shaping.

Verifies the core cost/behavior contract documented in llm_client.py:
- thinking enabled  -> {thinking:{type:enabled}} + reasoning_effort, NO temperature
- thinking disabled -> {thinking:{type:disabled}} + temperature, NO reasoning_effort
- per-call override wins; else deepseek_thinking_enabled; else legacy
  deepseek_reasoner_enabled; else v4 default enabled.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aimoon.adapters.driven.ai.pipeline.llm_client import (
    _apply_thinking,
    _resolve_thinking,
)


def _settings(**kw):
    defaults = {
        "deepseek_thinking_enabled": None,
        "deepseek_reasoner_enabled": None,
        "deepseek_temperature": 0.3,
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_thinking_enabled_sends_effort_not_temperature():
    s = _settings(deepseek_thinking_enabled=True)
    r = _resolve_thinking(s, None, "max")
    assert r["thinking"] == {"type": "enabled"}
    assert r["reasoning_effort"] == "max"
    assert r["temperature"] is None


def test_thinking_disabled_sends_temperature_not_effort():
    s = _settings(deepseek_thinking_enabled=False)
    r = _resolve_thinking(s, None, "high")
    assert r["thinking"] == {"type": "disabled"}
    assert r["reasoning_effort"] is None
    assert r["temperature"] == 0.3


def test_override_wins_over_settings():
    s = _settings(deepseek_thinking_enabled=True)
    # override False must disable even when settings say enabled
    r = _resolve_thinking(s, False, "high")
    assert r["thinking"] == {"type": "disabled"}
    assert r["reasoning_effort"] is None


def test_legacy_reasoner_enabled_alias_still_works():
    # deepseek_thinking_enabled unset, but old alias set true
    s = _settings(deepseek_reasoner_enabled=True)
    r = _resolve_thinking(s, None, "max")
    assert r["thinking"] == {"type": "enabled"}
    assert r["reasoning_effort"] == "max"


def test_default_is_enabled_for_v4():
    # nothing set -> v4 default thinking on
    s = _settings()
    r = _resolve_thinking(s, None, "high")
    assert r["thinking"] == {"type": "enabled"}


def test_apply_thinking_mutates_body():
    s = _settings(deepseek_thinking_enabled=False)
    body: dict = {"model": "deepseek-v4-flash", "messages": [], "max_tokens": 100}
    _apply_thinking(body, s, None, "high")
    assert body["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in body
    assert body["temperature"] == 0.3


def test_apply_thinking_enabled_omits_temperature():
    s = _settings(deepseek_thinking_enabled=True)
    body: dict = {"model": "deepseek-v4-flash", "messages": [], "max_tokens": 100}
    _apply_thinking(body, s, None, "max")
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "max"
    assert "temperature" not in body
