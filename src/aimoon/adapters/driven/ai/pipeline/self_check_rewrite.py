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
    llm: Callable[[str, str], str] | None = None,
    *,
    batch_llm: Callable[[list[tuple[str, str]]], list[str]] | None = None,
) -> str:
    """返回修正后的报告 markdown。

    两种调用模式（``batch_llm`` 优先，二选一）：
      - ``batch_llm``: (list[(system, user)]) -> list[corrected]
        一次性把全部疑点 prompt 交给底层批量执行（单线程 + 单事件循环 +
        单 httpx client + 并发 gather），把 N 次线程/循环/client 创建开销降到 1 次，
        wall-clock 从 N×latency 降到 ~1×latency。返回列表须与 mismatches 等长
        （失败位置回退空串），否则缺失位置保留原文。
      - ``llm``: (system_prompt, user_prompt) -> corrected_sentence
        逐条改正（旧契约，供单测与批量不可用时兜底）。
    """
    if not mismatches:
        return report_md

    prompts = [
        (
            _SYSTEM_PROMPT,
            _USER_TEMPLATE.format(
                facts_summary=_facts_summary(facts),
                snippet=mm.snippet,
                expected=mm.expected,
                metric=mm.metric,
            ),
        )
        for mm in mismatches
    ]

    if batch_llm is not None:
        corrections = list(batch_llm(prompts))
    elif llm is not None:
        corrections = [llm(sys_p, usr_p) for sys_p, usr_p in prompts]
    else:
        raise ValueError("self_check_rewrite 需要 llm 或 batch_llm 其一")

    result = report_md
    for mm, corrected in zip(mismatches, corrections):
        if not corrected:
            continue
        corrected = corrected.strip()
        # 安全护栏：改正句必须包含系统表给出的正确值，否则视为无效改正，保留原文。
        if mm.expected and mm.expected not in corrected:
            continue
        if mm.snippet in result:
            result = result.replace(mm.snippet, corrected)
    return result
