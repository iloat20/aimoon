"""Two-phase pipeline spec: ANALYSIS + COMPILE."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class Phase(StrEnum):
    """Pipeline v2 phases: ANALYSIS → SELF_CHECK → COMPILE."""

    ANALYSIS = "analysis"     # 规划+采集+分析
    SELF_CHECK = "self_check" # 轻量自检
    COMPILE = "compile"       # 终稿


# 报告章节结构 —— 代码侧维护,运行时格式化为
# `## 一、…` 标题块,与 compile.md 8 节大师级结构对齐。
_REPORT_SECTIONS = [
    ("一", "数据采集与叙事"),
    ("二", "法务会计审计"),
    ("三", "隐含市场预期反推"),
    ("四", "条件概率决策树"),
    ("五", "反向论证"),
    ("六", "投资策略"),
    ("七", "自我批判与情景应急"),
    ("八", "附录"),
]
_SECTIONS_MD = "\n".join(f"## {n}、{t}" for n, t in _REPORT_SECTIONS)


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
            Phase.SELF_CHECK,
            _load(Phase.SELF_CHECK),
            timeout_sec=60,    # 1 min: 轻量 JSON 自检
            required_outputs=["自检 JSON: passed + fixes_needed"],
        ),
        PhaseSpec(
            Phase.COMPILE,
            _load(Phase.COMPILE),
            timeout_sec=300,   # 5 min: 长文生成
            required_outputs=["完整 Markdown 终稿"],
        ),
    ]


def phase_system_prompt(phase: Phase, stock_md: str, prior: dict) -> str:
    template = _load(phase)
    # 仅当模板含对应占位符时才注入,避免向 hybrid prompt 注入冗余 JSON
    replacements: list[tuple[str, str]] = []
    if "{{ stock_info }}" in template:
        replacements.append(("{{ stock_info }}", stock_md))
    if "{{ tools_output }}" in template and "tools_output" in prior:
        import json
        tools_json = json.dumps(prior["tools_output"], ensure_ascii=False, default=str)
        replacements.append(("{{ tools_output }}", tools_json))
    if "{{ tools }}" in template and "tools_output" in prior:
        import json
        tools_json = json.dumps(prior["tools_output"], ensure_ascii=False, default=str)
        replacements.append(("{{ tools }}", tools_json))
    if "{{ prior }}" in template:
        import json
        if isinstance(prior, dict):
            prior_json = json.dumps(prior, ensure_ascii=False, default=str)
        else:
            prior_json = str(prior)
        replacements.append(("{{ prior }}", prior_json))
    if "{{ draft }}" in template:
        replacements.append(("{{ draft }}", str(prior.get("analysis_draft", "") or "")))
    if "{{ self_check_fixes }}" in template:
        import json
        fixes_json = json.dumps(prior.get("self_check_fixes", []), ensure_ascii=False)
        replacements.append(("{{ self_check_fixes }}", fixes_json))
    if "{{ tables_md }}" in template:
        replacements.append(("{{ tables_md }}", str(prior.get("tables_md", "") or "")))
    if "{{ summary }}" in template:
        tools_summary = prior.get("summary") or prior.get("tools_summary") or ""
        replacements.append(("{{ summary }}", str(tools_summary)))
    if "{{ sections }}" in template:
        replacements.append(("{{ sections }}", _SECTIONS_MD))
    compiled = template
    for k, v in replacements:
        compiled = compiled.replace(k, v)
    return compiled
