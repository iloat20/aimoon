"""Five-phase pipeline spec — Stage machine definition & system prompt loader."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class Phase(StrEnum):
    """Pipeline v2 五阶段。"""

    PLAN = "plan"
    COLLECT = "collect"
    ANALYSIS = "analysis"
    SELF_CHECK = "self_check"
    COMPILE = "compile"


PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load(phase: Phase) -> str:
    """Read prompt template for a phase; empty string if file missing."""
    p = PROMPTS_DIR / f"{phase.value}.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


@dataclass
class PhaseSpec:
    phase: Phase
    system_prompt_template: str
    tools: list[str] = field(default_factory=list)
    timeout_sec: int = 60
    max_retries: int = 2
    required_outputs: list[str] = field(default_factory=list)


def get_pipeline_phases() -> list[PhaseSpec]:
    """Return the ordered list of phase specs (五阶段 + 占位 system prompt)."""
    return [
        PhaseSpec(
            Phase.PLAN,
            _load(Phase.PLAN),
            timeout_sec=30,
            required_outputs=["子任务 ≥8"],
        ),
        PhaseSpec(
            Phase.COLLECT,
            _load(Phase.COLLECT),
            tools=["technicals", "financial_temporal", "peer_compare", "web_search"],
            timeout_sec=60,
            required_outputs=["三工具全非空"],
        ),
        PhaseSpec(
            Phase.ANALYSIS,
            _load(Phase.ANALYSIS),
            tools=["risk_quant", "valuation", "business_moat", "web_search"],
            timeout_sec=120,
            required_outputs=["三看空含触发条件", "三档估值"],
        ),
        PhaseSpec(
            Phase.SELF_CHECK,
            _load(Phase.SELF_CHECK),
            timeout_sec=30,
            required_outputs=["5 项 JSON 校验"],
        ),
        PhaseSpec(
            Phase.COMPILE,
            _load(Phase.COMPILE),
            timeout_sec=200,
            required_outputs=["长 Markdown"],
        ),
    ]
