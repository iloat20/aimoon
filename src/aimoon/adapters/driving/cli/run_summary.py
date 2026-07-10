"""运行结束时的采集健康概览（纯展示，无副作用）。

CollectorOrchestrator 已为每个数据源产出 ``CollectResult(status=...)``，
但默认只在采集阶段逐行 ``report`` 打印，运行结束后没有任何汇总。
本模块把分散的状态聚合成一张简洁的健康表，让"哪些源成功 / 失败 / 空"
一目了然——这正是"采集器永不阻断管线"设计下最缺的可观测性。
"""

from __future__ import annotations

from aimoon.core.domain.value_objects.collect_result import CollectResult

_STATUS_LABEL: dict[str, str] = {
    "success": "成功",
    "failed": "失败",
    "empty": "空",
    "timeout": "超时",
}

_WIDTH = 48


def _status_label(status: str) -> str:
    return _STATUS_LABEL.get(status, status)


def render_run_summary(
    results: list[CollectResult],
    *,
    total_elapsed_ms: int,
    skip_ai: bool,
) -> str:
    """渲染采集健康概览文本。

    Args:
        results: 各数据源采集结果（``CollectResult`` 列表）。
        total_elapsed_ms: 整次 run 的总耗时（毫秒）。
        skip_ai: 是否跳过了 AI 分析（仅影响脚注文案）。
    """
    if not results:
        return "采集健康概览: 无数据（可能为 mock 模式或采集未执行）。"

    lines: list[str] = [
        "─" * _WIDTH,
        "  采集健康概览",
        "─" * _WIDTH,
        f"  {'数据源':<14}{'状态':<6}{'条数':>6}",
        "  " + "-" * (_WIDTH - 2),
    ]

    ok = fail = empty = other = 0
    for r in results:
        if r.status == "success":
            ok += 1
        elif r.status == "failed":
            fail += 1
        elif r.status == "empty":
            empty += 1
        else:
            other += 1
        note = f"  {r.error}" if r.status in ("failed", "timeout") and r.error else ""
        lines.append(f"  {r.platform:<14}{_status_label(r.status):<6}{r.count:>6}{note}")

    total = len(results)
    lines.append("  " + "-" * (_WIDTH - 2))
    tail = f"  成功 {ok}/{total} · 失败 {fail} · 空 {empty}"
    if other:
        tail += f" · 其他 {other}"
    lines.append(tail)
    lines.append(
        f"  总耗时 {total_elapsed_ms / 1000:.1f}s" + (" · 已跳过AI" if skip_ai else "")
    )
    lines.append("─" * _WIDTH)
    return "\n".join(lines)
