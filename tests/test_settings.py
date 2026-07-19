"""Tests for quality-guard switches and AI provider resolution in Settings."""
from __future__ import annotations

from types import SimpleNamespace

from aimoon.adapters.driven.config.settings import (
    AIProviderConfig,
    get_settings,
    resolve_ai_provider,
)


def test_quality_switches_default():
    s = get_settings()
    assert s.direct_web_search_enabled is False
    assert s.reconcile_enabled is True
    assert s.self_check_rewrite_enabled is True


def _fake_settings(**kw):
    """Minimal fake Settings carrying both deepseek + longcat provider fields."""
    base = {
        "ai_provider": "deepseek",
        "deepseek_api_key": "",
        "deepseek_base_url": "https://api.deepseek.com",
        "deepseek_model": "deepseek-v4-flash",
        "deepseek_max_tokens": 24576,
        "deepseek_analysis_max_tokens": 4096,
        "deepseek_temperature": 0.3,
        "deepseek_thinking_enabled": None,
        "deepseek_analysis_effort": None,
        "longcat_api_key": "",
        "longcat_base_url": "https://api.longcat.chat",
        "longcat_model": "LongCat-2.0",
        "longcat_max_tokens": 24576,
        "longcat_analysis_max_tokens": 4096,
        "longcat_temperature": 0.3,
        "longcat_thinking_enabled": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_resolve_deepseek_default():
    cfg = resolve_ai_provider(_fake_settings(ai_provider="deepseek"))
    assert isinstance(cfg, AIProviderConfig)
    assert cfg.provider == "deepseek"
    assert cfg.supports_reasoning_effort is True
    assert cfg.chat_path == "/v1/chat/completions"
    assert cfg.chat_url == "https://api.deepseek.com/v1/chat/completions"
    # C3 归一后默认档位为 high(effort=None → "high");low/medium 也归一为 high。
    assert cfg.analysis_effort == "high"
    # C2 降本: 未显式配置思考开关时,DIRECT 直出默认关闭思考(thinking_enabled=False)。
    assert cfg.thinking_enabled is False
    assert cfg.model == "deepseek-v4-flash"


def test_resolve_longcat():
    cfg = resolve_ai_provider(_fake_settings(ai_provider="longcat"))
    assert cfg.provider == "longcat"
    # LongCat has no reasoning_effort param and no analysis_effort.
    assert cfg.supports_reasoning_effort is False
    assert cfg.analysis_effort is None
    assert cfg.chat_path == "/openai/v1/chat/completions"
    assert cfg.chat_url == "https://api.longcat.chat/openai/v1/chat/completions"
    assert cfg.model == "LongCat-2.0"
    assert cfg.thinking_enabled is None


def test_resolve_longcat_case_insensitive():
    cfg = resolve_ai_provider(_fake_settings(ai_provider="LongCat"))
    assert cfg.provider == "longcat"


def test_resolve_unknown_provider_falls_back_to_deepseek():
    cfg = resolve_ai_provider(_fake_settings(ai_provider="somethingelse"))
    assert cfg.provider == "deepseek"


def test_chat_url_strips_trailing_slash():
    cfg = resolve_ai_provider(
        _fake_settings(ai_provider="longcat", longcat_base_url="https://api.longcat.chat/")
    )
    assert cfg.chat_url == "https://api.longcat.chat/openai/v1/chat/completions"
