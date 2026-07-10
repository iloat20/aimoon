"""Render an analysis skeleton into readable Markdown.

Used in two paths:
1. Degraded mode - COMPILE fails, render skeleton directly (0 LLM).
2. Fast mode - use_fast/use_single_call skips COMPILE, render skeleton.
"""
from __future__ import annotations

import json
from typing import Any

from .skeleton_schema import AnalysisSkeleton


def _fmt2(x: Any) -> str:
    """格式化两位小数；None 或非数字返回 N/A（安全兜底，避免降级渲染崩溃）。"""
    if isinstance(x, (int, float)):
        return f"{x:.2f}"
    return "N/A"


def render_skeleton_md(raw: Any) -> str:
    """Render a skeleton dict (or None) into a readable Markdown report."""
    if raw is None:
        return "# 分析报告（降级）\n\n数据缺失，无法生成完整分析。"

    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return "# 分析报告（降级）\n\n骨架 JSON 解析失败，数据暂不可用。"
    elif isinstance(raw, dict):
        data = raw
    else:
        return "# 分析报告（降级）\n\n骨架数据异常。"

    try:
        sk = AnalysisSkeleton.model_validate(data)
    except Exception:
        return _render_raw(data)

    try:
        return _build_skeleton_md(sk)
    except Exception:
        # 安全网：渲染任何异常都不得中断降级报告（0-LLM 降级保证）
        return _render_raw(data)


def _build_skeleton_md(sk: AnalysisSkeleton) -> str:
    lines: list[str] = ["# 分析报告（骨架渲染）\n"]

    # Narratives
    n = sk.narratives
    lines.append("## 三层叙事框架\n")
    for label, nar in [("宏观", n.macro), ("行业", n.industry), ("企业alpha", n.alpha)]:
        lines.append(f"### {label}（P={nar.probability:.0%}）")
        lines.append(f"- 共识：{nar.consensus}")
        lines.append(f"- 我们的解读：{nar.our_view}")
        lines.append(f"- 证伪阈值：{nar.falsify}\n")
    lines.append(f"**复合看多概率：{sk.composite_prob:.0%}**\n")

    # Forensic audit
    fa = sk.forensic_audit
    lines.append("## 法务会计审计\n")
    for item in fa.items:
        lines.append(f"- {item.item}：{item.status} - {item.detail}")
    d = fa.dupont
    if d.net_margin is not None:
        lines.append(
            f"\n**杜邦拆解**：净利率 {_fmt2(d.net_margin)}"
            f" * 周转率 {_fmt2(d.turnover)} * 杠杆 {_fmt2(d.leverage)}"
        )
    lines.append(f"\n**盈利质量评分：{fa.quality_score}/10**")
    if fa.red_flags:
        lines.append(f"**红旗**：{', '.join(fa.red_flags)}\n")

    # Valuation
    v = sk.valuation
    t = v.targets
    lines.append("## 估值与目标价\n")
    lines.append(
        f"- 保守：{_fmt2(t.conservative)} | 中性：{_fmt2(t.neutral)}"
        f" | 乐观：{_fmt2(t.optimistic)}"
    )
    if v.implied_g is not None:
        lines.append(f"- 隐含增长率 g*：{v.implied_g:.2%}")
    if v.peer_pe:
        pe_str = "、".join(f"{k} {val}" for k, val in v.peer_pe.items())
        lines.append(f"- 同业 PE 对比：{pe_str}")
    lines.append(f"- 预期差判断：{v.expectation_gap}")
    if v.sensitivity:
        lines.append("- 敏感度分析：")
        for s in v.sensitivity:
            lines.append(f"  - {s.param}：{s.impact}")
    lines.append("")

    # Kelly
    k = sk.kelly
    lines.append("## 仓位量化（Kelly）\n")
    lines.append(f"- 评级：{k.rating}")
    lines.append(
        f"- b={k.b} | p={k.p:.0%} | q={k.q:.0%} | f*={k.f_star:.2%} | 建议仓位={k.position:.2%}\n"
    )

    # Red team
    if sk.red_team:
        lines.append("## 反向论证\n")
        for rt in sk.red_team:
            lines.append(f"- 看多：{rt.bull} -> 反证：{rt.bear}")
        lines.append("")

    # Decision tree
    if sk.decision_tree:
        lines.append("## 决策树\n")
        for br in sk.decision_tree:
            lines.append(
                f"- {br.event}：触发={br.trigger}（P={br.prob}）"
                f"-> {br.action_triggered} / 否则 {br.action_else}"
            )
        lines.append("")

    # Self-critique（自我批判辩论：空头攻击 -> 裁判回应）
    sc = sk.self_critique
    if sc.bear_attacks or sc.judge:
        lines.append("## 自我批判辩论\n")
        for ba in sc.bear_attacks:
            lines.append(f"- 空头攻击「{ba.assumption}」：{ba.attack}")
        if sc.judge:
            lines.append(f"\n**裁判回应**：{sc.judge}")
        lines.append("")

    # Stress test（极端压力测试：情景 -> 底线价 -> 结论）
    st = sk.stress_test
    if st.scenario or st.verdict or st.floor_price is not None:
        lines.append("## 极端压力测试\n")
        if st.scenario:
            lines.append(f"- 情景：{st.scenario}")
        if st.stress_fcf is not None:
            lines.append(f"- 压力自由现金流：{_fmt2(st.stress_fcf)}")
        if st.dividend_coverage is not None:
            lines.append(f"- 股息覆盖率：{_fmt2(st.dividend_coverage)}")
        if st.floor_price is not None:
            _dd = (
                f"（下行 {st.floor_downside_pct:.1%}）"
                if st.floor_downside_pct is not None else ""
            )
            lines.append(f"- 底线价：{_fmt2(st.floor_price)}{_dd}")
        if st.verdict:
            lines.append(f"- 结论：{st.verdict}")
        lines.append("")

    # Missing-data audit & inference (compile.md 要求展开，降级路径不得丢失)
    if sk.data_inference:
        lines.append("## 缺失数据反推\n")
        for di in sk.data_inference:
            _base = _fmt2(di.base) if di.base is not None else "N/A"
            lines.append(
                f"- {di.field}：{di.formula}（基准 {_base}，影响：{di.price_impact}）"
            )
        lines.append("")
    if sk.data_audit:
        lines.append("## 数据审计\n")
        lines.append("```json")
        lines.append(json.dumps(sk.data_audit, ensure_ascii=False, indent=2)[:1500])
        lines.append("```\n")

    lines.append(
        "> WARNING: 本报告由 AI 自动生成（骨架渲染模式），"
        "数据与观点仅基于公开信息，不构成投资建议。"
    )
    return "\n".join(lines)


def _render_raw(data: dict) -> str:
    """Best-effort render when schema validation fails - dump key fields."""
    lines = ["# 分析报告（降级 - 骨架校验未通过）\n"]
    lines.append("```json")
    lines.append(json.dumps(data, ensure_ascii=False, indent=2)[:3000])
    lines.append("```\n")
    lines.append("> WARNING: 本报告由 AI 自动生成（降级模式），不构成投资建议。")
    return "\n".join(lines)
