"""Shared types for the v2 pipeline orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class AnalyzerRuntime(Protocol):
    """Minimal runtime surface the pipeline needs from the analyzer facade."""

    _settings: Any
    _provided_settings: Any | None
    _http: Any
    api_url: str
    api_key: str


@dataclass
class _ToolContext:
    """工具采集 + 表格渲染 + 摘要的共享产物(skeleton 与 direct 两条流复用)。"""

    tool_results: dict[str, object]
    partial: bool
    tables_md: str
    summary: str
    body: str  # user 消息主体(不含各阶段各自的「# 输出要求」尾巴)


@dataclass
class PipelineContext:
    phase_results: dict[str, dict[str, object]] = field(default_factory=dict)
    partial_phases: list[str] = field(default_factory=list)
    final_markdown: str = ""
    system_tables_md: str = ""
    skeleton: dict | None = None
    credibility: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        # 系统预渲染表不再内联进 final_markdown,改为经 system_tables_md 透传,
        # 由报告模板渲染为独立的「数据底稿」卡片,前置在 AI 报告之前。
        return {
            "final_markdown": self.final_markdown or "",
            "system_tables_md": self.system_tables_md,
            "phase_results": self.phase_results,
            "partial_phases": self.partial_phases,
            "skeleton": self.skeleton,
            "credibility": self.credibility,
        }
