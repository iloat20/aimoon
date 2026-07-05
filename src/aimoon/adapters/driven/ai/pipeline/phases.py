"""Two-phase pipeline spec: ANALYSIS + COMPILE."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class Phase(StrEnum):
    """Pipeline v2 两阶段."""

    ANALYSIS = "analysis"   # 规划+采集+分析+自检
    COMPILE = "compile"     # 终稿


PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load(phase: Phase) -> str:
    p = PROMPTS_DIR / f"{phase.value}.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


@dataclass
class PhaseSpec:
    phase: Phase
    system_prompt_template: str
    tools: list[str] = field(default_factory=list)
    timeout_sec: int = 120
    max_retries: int = 2
    required_outputs: list[str] = field(default_factory=list)


def get_pipeline_phases() -> list[PhaseSpec]:
    return [
        PhaseSpec(
            Phase.ANALYSIS,
            _load(Phase.ANALYSIS),
            tools=[
                "technicals", "financial_temporal", "peer_compare",
                "risk_quant", "valuation", "business_moat", "web_search",
            ],
            timeout_sec=240,   # 4 min: 并行工具 + LLM 思考 + 自检 + 重跑
            required_outputs=["分析初稿 + 自检 JSON", "三看空含触发", "三张核心表"],
        ),
        PhaseSpec(
            Phase.COMPILE,
            _load(Phase.COMPILE),
            timeout_sec=300,   # 5 min: 长文生成
            required_outputs=["完整 Markdown 终稿"],
        ),
    ]


def phase_system_prompt(phase: Phase, stock_md: str, prior: dict) -> str:
    import json
    template = _load(phase)
    tools_serialized = ""
    if phase == Phase.ANALYSIS and "tools_output" in prior:
        tools_serialized = json.dumps(prior["tools_output"], ensure_ascii=False, default=str)
    compiled = (
        template
        .replace("{{ stock_info }}", stock_md)
        .replace("{{ tools_output }}", tools_serialized)
        .replace("{{ tools }}", tools_serialized)
        .replace(
            "{{ prior }}",
            json.dumps(prior, ensure_ascii=False, default=str)
            if isinstance(prior, dict) else str(prior),
        )
        .replace("{{ draft }}", str(prior.get("analysis_draft", "") or ""))
        .replace(
            "{{ self_check_fixes }}",
            json.dumps(prior.get("self_check_fixes", []), ensure_ascii=False),
        )
    )
    return compiled
