"""Compact Markdown summaries of tool outputs for the v2 pipeline."""

from __future__ import annotations

from .utils import is_partial


def _fmt_yi(v: object) -> str:
    """Format a yuan amount: 亿 if large, else raw 2-decimal; N/A on None."""
    if v is None:
        return "N/A"
    if isinstance(v, (int, float)) and abs(v) >= 1e8:
        return f"{v / 1e8:.1f}亿"
    return f"{v:.2f}"


def senti_summary(senti: object) -> str:
    """Format sentiment tool output into a compact bullet summary."""
    if not isinstance(senti, dict) or is_partial(senti):
        return "- 社媒情感分析: 数据缺失(无可用舆情文本)"
    total = senti.get("total") or 0
    if not total:
        return "- 社媒情感分析: 样本为空"
    lines = [
        f"- 整体情绪: {senti.get('label', 'N/A')}"
        f"(指数 {senti.get('sentiment_index', 0)},引擎 {senti.get('engine', 'N/A')})",
        f"- 分布: 正面 {senti.get('pos', 0)} / 负面 {senti.get('neg', 0)}"
        f"/ 中性 {senti.get('neu', 0)}(共 {total} 条)",
    ]
    kws = senti.get("top_keywords") or []
    if kws:
        lines.append("- 高频词: " + "、".join(f"{k['word']}({k['count']})" for k in kws[:8]))
    nw = senti.get("neg_words") or []
    if nw:
        lines.append("- 负面词: " + "、".join(f"{w}({c})" for w, c in nw[:5]))
    return "\n".join(lines)


def fcf_summary(fcf: object) -> str:
    """Format FCF/dividend tool output into a compact bullet summary."""
    if not isinstance(fcf, dict) or is_partial(fcf):
        return "- 自由现金流与股息: 数据缺失(缺 OCF 或分红科目)"
    ocf = fcf.get("ocf")
    fcf_v = fcf.get("fcf")
    lines = [
        f"- 经营现金流 OCF: {_fmt_yi(ocf)} | 自由现金流 FCF: {_fmt_yi(fcf_v)}",
    ]
    pay = fcf.get("payout_ratio")
    dy = fcf.get("dividend_yield")
    if pay is not None:
        if dy is not None:
            lines.append(f"- 股息支付率: {pay * 100:.1f}% | 股息率: {dy * 100:.1f}%")
        else:
            lines.append(f"- 股息支付率: {pay * 100:.1f}%")
    cover = fcf.get("fcf_cover")
    if cover is not None:
        note = "可持续" if cover >= 1.0 else f"⚠️ 不可持续(FCF 仅覆盖 {cover:.2f} 倍)"
        lines.append(f"- FCF 覆盖分红: {cover:.2f} 倍 → {note}")
    return "\n".join(lines)


def scenario_summary(scenario: object) -> str:
    """Format scenario probability / risk-reward tool output into a summary."""
    if not isinstance(scenario, dict) or is_partial(scenario):
        return "- 情景概率与风险收益比: 数据缺失(缺估值目标价)"
    exp = scenario.get("expected_target")
    rr = scenario.get("risk_reward_ratio")
    down = scenario.get("downside_neutral_pct")
    up = scenario.get("upside_optimistic_pct")
    lines = []
    if exp is not None:
        lines.append(f"- 加权期望目标价: {exp} 元(期望 PE {scenario.get('expected_pe')})")
    if down is not None or up is not None:
        d = f"{down:+.1f}%" if isinstance(down, (int, float)) else "N/A"
        u = f"{up:+.1f}%" if isinstance(up, (int, float)) else "N/A"
        rr_txt = f" → 非对称比 {rr:.2f}" if rr is not None else ""
        lines.append(f"- 风险收益比: 中性下行 {d} / 乐观上行 {u}{rr_txt}")
    targets = scenario.get("targets") or {}
    if targets:
        parts = []
        name_map = {"conservative": "保守", "neutral": "中性", "optimistic": "乐观"}
        for tier in ("conservative", "neutral", "optimistic"):
            t = targets.get(tier) or {}
            p = t.get("probability")
            if p is not None:
                parts.append(f"{name_map.get(tier, tier)}{t.get('price')}({p}%)")
        if parts:
            lines.append("- 三档情景: " + " / ".join(parts))
    return "\n".join(lines) if lines else "- 情景概率与风险收益比: 数据缺失"


def research_divergence(si: object) -> str:
    """量化机构研报分歧:EPS 预测区间 + 评级分布。"""
    research = getattr(si, "research", None)
    reports = (research.reports if research else None) or []
    if not reports:
        return "- 机构研报分歧: 数据缺失(无研报)"
    buys = sum(1 for r in reports if "买入" in (r.rating or "") or "推荐" in (r.rating or ""))
    holds = sum(1 for r in reports if "增持" in (r.rating or ""))
    neutrals = sum(1 for r in reports if "中性" in (r.rating or "") or "持有" in (r.rating or ""))
    eps_list = [float(r.eps_this_yr) for r in reports if getattr(r, "eps_this_yr", 0)]
    lines = [
        f"- 评级分布: 买入 {buys} / 增持 {holds} / 中性 {neutrals}(共 {len(reports)} 篇)",
    ]
    if eps_list:
        lo, hi = min(eps_list), max(eps_list)
        avg = sum(eps_list) / len(eps_list)
        spread = (hi - lo) / lo * 100 if lo else 0.0
        lines.append(
            f"- 当年 EPS 预测: 区间 [{lo:.2f}, {hi:.2f}], 均值 {avg:.2f}, "
            f"分歧幅度 {spread:.1f}%(分歧>15% 视为预期差大)"
        )
    return "\n".join(lines)


def extract_tool_summary(results: dict) -> str:
    """Generate a short (~200 chars) text summary of non-tabular tool outputs.

    Helps the LLM produce analysis without needing the full tool JSON.
    """
    parts: list[str] = []
    t = results.get("technicals") or {}
    if isinstance(t, dict):
        trend = t.get("trend") or ""
        rsi = t.get("rsi14")
        main = t.get("main_net_5d")
        if trend:
            parts.append(f"趋势={trend}")
        if rsi is not None:
            parts.append(f"RSI={rsi:.1f}" if isinstance(rsi, (int, float)) else f"RSI={rsi}")
        if main is not None:
            if isinstance(main, (int, float)):
                parts.append(f"主力5日={main / 1e8:.2f}亿")
            else:
                parts.append(f"主={main}")
    r = results.get("risk_quant") or {}
    if isinstance(r, dict) and isinstance(r.get("bears"), list):
        nb = len(r["bears"])
        if nb:
            parts.append(f"看空={nb}条")
    m = results.get("business_moat") or {}
    if isinstance(m, dict):
        moat = m.get("moat_sources") or []
        if isinstance(moat, list) and moat:
            parts.append(f"护城河={','.join(str(x) for x in moat[:3])}")
        ocf_q = m.get("ocf_quality")
        if ocf_q:
            parts.append(f"OCF质量={ocf_q}")
    # 自由现金流 / 股息
    f = results.get("fcf_dividend") or {}
    if isinstance(f, dict) and not is_partial(f):
        fcf = f.get("fcf")
        pay = f.get("payout_ratio")
        dy = f.get("dividend_yield")
        if fcf is not None:
            parts.append(f"FCF={fcf/1e8:.1f}亿" if abs(fcf) >= 1e8 else f"FCF={fcf:.1f}")
        if pay is not None:
            parts.append(f"股息支付率={pay*100:.1f}%")
        if dy is not None:
            parts.append(f"股息率={dy*100:.1f}%")
    # 情景概率 / 风险收益比
    s = results.get("scenario_prob") or {}
    if isinstance(s, dict) and not is_partial(s):
        exp = s.get("expected_target")
        rr = s.get("risk_reward_ratio")
        if exp is not None:
            parts.append(f"加权期望目标价={exp}")
        if rr is not None:
            parts.append(f"风险收益比={rr}")
    # 舆情情感
    senti = results.get("sentiment") or {}
    if isinstance(senti, dict) and not is_partial(senti):
        label = senti.get("label")
        idx = senti.get("sentiment_index")
        if label:
            parts.append(f"舆情情绪={label}(指数{idx})")
    return ", ".join(parts) if parts else "N/A"
