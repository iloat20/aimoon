"""LLM 定点重写：只让 LLM 改正错句，再用字符串替换换掉原文对应句。

仅处理 report_reconciler.reconcile 产出的 mismatches；不重写全文、不发挥。
若 LLM 没有返回有效的改正句（改正句须包含系统表给出的正确值），则保留原文——
宁可不改也不错改。
"""

from __future__ import annotations

from collections.abc import Callable

from aimoon.adapters.driven.ai.pipeline.report_reconciler import Mismatch

_SYSTEM_PROMPT = "你只改正错句，不发挥，不要重写全文，不要加解释。"

_USER_TEMPLATE = """\
以下是系统事实表中的正确值：
{facts_summary}

报告中的疑点原句：
{snippet}

正确值应来自上面的系统事实表（期望：{expected}，指标：{metric}）。

只输出改正后的那一句话。"""


def _facts_summary(facts: dict) -> str:
    if not facts:
        return "（无）"
    return "\n".join(f"- {k}: {v}" for k, v in facts.items())


def self_check_rewrite(
    report_md: str,
    mismatches: list[Mismatch],
    facts: dict,
    llm: Callable[[str, str], str],
) -> str:
    """返回修正后的报告 markdown。

    llm: (system_prompt, user_prompt) -> corrected_sentence(str)
    """
    result = report_md
    for mm in mismatches:
        corrected = llm(
            _SYSTEM_PROMPT,
            _USER_TEMPLATE.format(
                facts_summary=_facts_summary(facts),
                snippet=mm.snippet,
                expected=mm.expected,
                metric=mm.metric,
            ),
        )
        if not corrected:
            continue
        corrected = corrected.strip()
        # 安全护栏：改正句必须包含系统表给出的正确值，否则视为无效改正，保留原文。
        if mm.expected and mm.expected not in corrected:
            continue
        if mm.snippet in result:
            result = result.replace(mm.snippet, corrected)
    return result
