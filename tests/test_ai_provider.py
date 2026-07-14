"""Tests for provider-aware transport in DeepSeekApiClient.

Verifies the refactor where model / temperature / max_tokens are read from
``provider_config`` (resolved via ``resolve_ai_provider``) when supplied, and
fall back to the legacy DeepSeek ``settings`` fields when no config is given
(keeps old tests that construct the client without a config working).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from aimoon.adapters.driven.ai.api_client import DeepSeekApiClient
from aimoon.adapters.driven.config.settings import AIProviderConfig


def _cfg(
    provider: str = "longcat",
    model: str = "LongCat-2.0",
    temperature: float = 0.5,
    max_tokens: int = 1234,
) -> AIProviderConfig:
    return AIProviderConfig(
        provider=provider,
        api_key="k",
        base_url="https://api.longcat.chat",
        chat_path="/openai/v1/chat/completions",
        model=model,
        max_tokens=max_tokens,
        analysis_max_tokens=4096,
        temperature=temperature,
        thinking_enabled=None,
        supports_reasoning_effort=False,
        analysis_effort=None,
    )


def _fake_settings(**kw):
    base = {
        "deepseek_model": "deepseek-v4-flash",
        "deepseek_temperature": 0.3,
        "deepseek_max_tokens": 24576,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _client(**kw) -> DeepSeekApiClient:
    return DeepSeekApiClient(
        api_url="https://api.longcat.chat/openai/v1/chat/completions",
        api_key="k",
        settings=_fake_settings(),
        http_client=AsyncMock(),
        **kw,
    )


def test_client_uses_cfg_fields_when_present():
    client = _client(provider_config=_cfg())
    assert client._model == "LongCat-2.0"
    assert client._temperature == 0.5
    assert client._max_tokens == 1234


def test_client_falls_back_to_settings_without_cfg():
    client = _client()  # no provider_config
    assert client._model == "deepseek-v4-flash"
    assert client._temperature == 0.3
    assert client._max_tokens == 24576


def test_client_cfg_overrides_settings_values():
    # even if settings carry different deepseek values, cfg wins
    client = _client(
        provider_config=_cfg(model="LongCat-2.0", temperature=0.9, max_tokens=512),
    )
    assert client._model == "LongCat-2.0"
    assert client._temperature == 0.9
    assert client._max_tokens == 512
