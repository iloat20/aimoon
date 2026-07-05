"""Phase prompt loader + formatter for pipeline v2.

每个 ``phase_<name>.md`` 的 system prompt 模板通过 ``phase_system_prompt()``
注入个股快照与上游阶段输出,供 orchestrator 拼装 LLM 调用。
"""

from __future__ import annotations

from ..phases import Phase, _load


def phase_system_prompt(phase: Phase, stock_md: str, prior: dict[str, object]) -> str:
    """返回注入后的 system prompt。

    模板占位符:
    - ``{{ stock_info }}`` → 渲染后的个股快照文本
    - ``{{ prior_<phase> }}`` → 上游阶段输出摘要
    """
    template = _load(phase)
    prior_text = _render_prior(prior)
    return template.replace("{{ stock_info }}", stock_md).replace("{{ prior }}", prior_text)


def _render_prior(prior: dict[str, object]) -> str:
    if not prior:
        return "(无)"
    parts: list[str] = []
    for phase_value, output in prior.items():
        snippet = str(output)[:500] if output else ""
        parts.append(f"[{phase_value}]\n{snippet}" if snippet else f"[{phase_value}](#empty)")
    return "\n\n".join(parts)
