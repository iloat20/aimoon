"""Unit tests for the DeepSeek thinking-mode request shaping.

Verifies the core cost/behavior contract documented in llm_client.py:
- thinking enabled  -> {thinking:{type:enabled}} + reasoning_effort, NO temperature
- thinking disabled -> {thinking:{type:disabled}} + temperature, NO reasoning_effort
- per-call override wins; else deepseek_thinking_enabled; else v4 default enabled.
"""

from __future__ import annotations

from types import SimpleNamespace

from aimoon.adapters.driven.ai.pipeline.llm_client import (
    _apply_thinking,
    _resolve_thinking,
)


def _settings(**kw):
    defaults = {
        "deepseek_thinking_enabled": None,
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


# ---- LongCat: OpenAI-compatible, has thinking but NO reasoning_effort ----


def _lc_settings(**kw):
    defaults = {
        "ai_provider": "longcat",
        "longcat_thinking_enabled": None,
        "longcat_temperature": 0.3,
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_longcat_thinking_enabled_omits_effort_and_temperature():
    # longcat_thinking_enabled=None -> model default (enabled); LongCat only uses
    # thinking:{type}, never reasoning_effort, and temperature is ignored while
    # thinking is on.
    s = _lc_settings(longcat_thinking_enabled=None)
    r = _resolve_thinking(s, None, "max")
    assert r["thinking"] == {"type": "enabled"}
    assert r["reasoning_effort"] is None
    assert r["temperature"] is None


def test_longcat_thinking_disabled_sends_temperature():
    s = _lc_settings(longcat_thinking_enabled=False)
    r = _resolve_thinking(s, None, "max")
    assert r["thinking"] == {"type": "disabled"}
    assert r["reasoning_effort"] is None
    assert r["temperature"] == 0.3


def test_longcat_override_wins_over_settings():
    s = _lc_settings(longcat_thinking_enabled=True)
    r = _resolve_thinking(s, False, "max")
    assert r["thinking"] == {"type": "disabled"}


def test_longcat_apply_omits_effort_and_temperature():
    s = _lc_settings(longcat_thinking_enabled=None)
    body: dict = {"model": "LongCat-2.0", "messages": [], "max_tokens": 100}
    _apply_thinking(body, s, None, "max")
    assert body["thinking"] == {"type": "enabled"}
    assert "reasoning_effort" not in body
    assert "temperature" not in body
