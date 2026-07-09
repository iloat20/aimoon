"""Shared types for the v2 pipeline orchestrator."""

from __future__ import annotations

from typing import Any, Protocol


class AnalyzerRuntime(Protocol):
    """Minimal runtime surface the pipeline needs from the analyzer facade."""

    _settings: Any
    _provided_settings: Any | None
    _http: Any
    api_url: str
    api_key: str
