"""Render the stock snapshot Markdown for the v2 pipeline."""

from __future__ import annotations

from pathlib import Path

from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis

from ..prompt_builder import build_data_dict


def render_stock_context(
    si: StockAnalysis,
    reports: dict | None = None,
    financial_md_path: Path | None = None,
) -> str:
    """Render the ``# 标的快照`` Markdown block for the ANALYSIS prompt."""
    data = build_data_dict(si, reports, financial_md_path).to_dict()
    lines: list[str] = [f"# 标的快照 {si.name or si.symbol}"]
    quote = data.get("quote") or {}
    if quote.get("price"):
        lines.append(
            f"- 最新价: {quote.get('price')} | "
            f"涨跌: {quote.get('change_pct')}% | PE: {quote.get('pe')}"
        )
    # 注意:报告期/营收/净利/ROE 等财务字段不再在此重复列出 —— 工具结果
    # `financial_temporal` 已注入同字段,这里只保留跨维度事实(资金/行业)
    # 与 snapshot 独有的舆情,避免 input token 重复。
    cf = data.get("capital_flow") or {}
    if cf.get("main_net_5d"):
        lines.append(f"- 近5日主力净流入: {cf['main_net_5d'] / 1e8:.2f} 亿元")
    if data.get("industry"):
        lines.append(f"- 行业: {data['industry']}")
    # 舆情雪球/头条近 N 条标题摘要(跨维度事实,不在工具结果里)
    posts = getattr(si, "social_posts", None)
    if posts:
        sample = "；".join(
            (p.title or p.content or "")[:30] for p in posts[:3]
        )
        if sample:
            lines.append(f"- 舆情摘要: {sample}")
    if getattr(si, "history_financial", None):
        lines.append("- 历史财务时序(近 N 年报):")
        for f in (si.history_financial or [])[:5]:
            rev_str = f"{f.revenue / 1e8:.1f}亿" if f.revenue else "N/A"
            lines.append(f"  - {f.report_period}:营收 {rev_str} | "
                        f"ROE {f.roe}% | EPS {f.eps}")
    return "\n".join(lines)
