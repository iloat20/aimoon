"""Render tool JSON results into Markdown tables (zero LLM tokens).

Three core tables:
1. Financial timeline (from financial_temporal.years)
2. Peer comparison (from peer_compare.peers)
3. Valuation targets (from valuation.fcfe_targets + assumptions)
"""

from __future__ import annotations

from typing import Any

COLUMNS = (
    "报告期 | 营收(亿) | 营收同比(%) | 净利润(亿) | 净利同比(%) | ROE(%) | EPS | 经营现金流(亿)"
)


def render_financial_temporal(data: Any) -> str:
    """Render financial_temporal.years to Markdown table.

    Columns: {COLUMNS}
    """
    if not isinstance(data, dict):
        return ""
    years = data.get("years") or []
    if not isinstance(years, list) or not years:
        return ""

    lines: list[str] = [
        "## 近年财务时序表",
        "",
        f"| {COLUMNS} |",
        "|--------|----------|-------------|------------|-------------|--------|-----|----------------|",
    ]
    for y in years:
        if not isinstance(y, dict):
            continue
        period = str(y.get("period") or y.get("report_period") or "N/A")
        rev = _fmt_num(y.get("rev") or y.get("revenue"))
        rev_yoy = _fmt_pct(y.get("rev_yoy") or y.get("revenue_yoy"))
        np_ = _fmt_num(y.get("np") or y.get("net_profit"))
        np_yoy = _fmt_pct(y.get("np_yoy") or y.get("net_profit_yoy"))
        roe_raw = y.get("roe")
        roe = _fmt_pct(roe_raw * 100 if roe_raw else None)  # roe 存储为小数,显示为 %
        eps = _fmt_num(y.get("eps"))
        ocf = _fmt_num(y.get("ocf") or y.get("operating_cf"))
        lines.append(
            f"| {period} | {rev} | {rev_yoy} | {np_} | {np_yoy} | {roe} | {eps} | {ocf} |"
        )
    return "\n".join(lines)


def render_peer_comparison(data: Any) -> str:
    """Render peer_compare.peers to Markdown table.

    Columns: 公司 | 最新价 | PE | PB | ROE(%) | 营收增速(%) | 净利增速(%) | 市值(亿)
    """
    if not isinstance(data, dict):
        return ""
    peers = data.get("peers") or []
    if not isinstance(peers, list) or not peers:
        return ""

    lines: list[str] = [
        "## 同行竞品对比表",
        "",
        "| 公司 | 最新价 | PE | PB | ROE(%) | 营收增速(%) | 净利增速(%) | 市值(亿) |",
        "|------|--------|----|----|--------|------------|------------|----------|",
    ]
    for p in peers:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "N/A")
        price = _fmt_num(p.get("price") or p.get("latest_price"))
        pe = _fmt_num(p.get("pe"))
        pb = _fmt_num(p.get("pb"))
        roe = _fmt_pct(p.get("roe"))
        rev_g = _fmt_pct(p.get("rev_g") or p.get("revenue_growth"))
        np_g = _fmt_pct(p.get("np_g") or p.get("profit_growth"))
        mcap = _fmt_num(p.get("mcap") or p.get("market_cap"))
        lines.append(
            f"| {name} | {price} | {pe} | {pb} | {roe} | {rev_g} | {np_g} | {mcap} |"
        )
    return "\n".join(lines)


def render_valuation_targets(data: Any) -> str:
    """Render valuation.fcfe_targets + assumptions to Markdown table.

    Columns: 档位 | PE | 目标价(元) | 概率(%)
    """
    if not isinstance(data, dict):
        return ""
    targets = data.get("fcfe_targets") or data.get("targets") or {}
    if not isinstance(targets, dict) or not targets:
        return ""

    assumptions = data.get("fcfe_assumptions") or data.get("assumptions") or {}
    # 用 is None 判断而非 or,避免 0.0 等合法 falsy 值被误判为缺失。
    disc = assumptions.get("discount_rate")
    if disc is None:
        disc = assumptions.get("r")
    g = assumptions.get("growth")
    if g is None:
        g = assumptions.get("g")
    tg = assumptions.get("terminal_growth")
    if tg is None:
        tg = assumptions.get("terminal_g")

    lines: list[str] = [
        "## 估值三档表",
        "",
    ]
    if disc is not None or g is not None:
        # 折现率/增速以小数存储,此处显式 ×100 显示为百分比(避免 0.1 误读为 0.1%)
        disc_s = f"{disc * 100:.1f}%" if disc is not None else "N/A"
        tg_s = f"{tg * 100:.1f}%" if tg is not None else "N/A"
        lines.append(f"*假设:折现率={disc_s},永续增速封顶={tg_s}*")
        lines.append("")
    lines.extend(
        [
            "| 档位 | PE | 目标价(元) | 概率(%) |",
            "|------|----|------------|---------|",
        ]
    )
    for tier in ("conservative", "neutral", "optimistic"):
        if tier not in targets:
            continue
        t = targets[tier]
        if isinstance(t, (int, float)):
            # Simple numeric target
            lines.append(f"| {tier} | - | {_fmt_num(t)} | - |")
        elif isinstance(t, dict):
            pe = _fmt_num(t.get("pe"))
            price = _fmt_num(t.get("price") or t.get("target_price"))
            prob = _fmt_pct(t.get("probability") or t.get("prob"))
            lines.append(f"| {tier} | {pe} | {price} | {prob} |")
    return "\n".join(lines)


def render_financial_statements(financial: Any) -> str:
    """Render the annual-report three statements (income / balance / cash flow)
    from ``financial.statements`` into three Markdown tables.

    The detailed line items were previously discarded during collection, so the
    AI never saw the three statements. Now they flow into ``FinancialData.statements``
    and are rendered here (and injected into the AI prompt via ``tables_md``),
    so the analysis can cite real 利润表/资产负债表/现金流量表 figures.
    Returns '' when no statements are present.
    """
    if not isinstance(financial, object):
        return ""
    stmts = getattr(financial, "statements", None) or {}
    if not isinstance(stmts, dict) or not stmts:
        return ""

    titles = {
        "income": "利润表(年报)",
        "balance": "资产负债表(年报)",
        "cash_flow": "现金流量表(年报)",
    }
    blocks: list[str] = []
    for key in ("income", "balance", "cash_flow"):
        rows = stmts.get(key) or []
        if not rows:
            continue
        lines = [
            f"## {titles[key]}",
            "",
            "| 项目 | 金额(亿元/元) | 同比(%) |",
            "|------|-----------|---------|",
        ]
        for r in rows:
            if not isinstance(r, dict):
                continue
            val = r.get("value") or 0.0
            yoy = r.get("yoy")
            item = str(r.get("item", ""))
            # 每股指标(基本每股收益等)单位为 元,不 ÷1e8;其余金额统一以亿元展示
            if "每股收益" in item or item.upper().endswith("EPS"):
                val_s = f"{float(val):.2f}"
            else:
                val_s = _fmt_yi_amount(val)
            yoy_s = "—" if yoy is None else _fmt_pct(yoy)
            lines.append(f"| {item} | {val_s} | {yoy_s} |")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _fmt_num(v: Any) -> str:
    """Format a number for display. Returns 'N/A' on None/invalid."""
    if v is None:
        return "N/A"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    if n == 0:
        return "0"
    # Large numbers: convert to 亿
    if abs(n) >= 1e8:
        return f"{n / 1e8:.1f}"
    if abs(n) >= 100:
        return f"{n:.1f}"
    return f"{n:.2f}"


def _fmt_pct(v: Any) -> str:
    """Format a percentage. Returns 'N/A' on None."""
    if v is None:
        return "N/A"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{n:.1f}"


def _pct(v: Any) -> str:
    """Format a fraction as a percent string (0.25 -> '25.0%'). 'N/A' on None."""
    if v is None:
        return "N/A"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "N/A"
    return f"{n * 100:.1f}%"


def _signed_pct(v: Any) -> str:
    """Format a fraction as a signed percent (0.05 -> '+5.0%'). 'N/A' on None."""
    if v is None:
        return "N/A"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "N/A"
    sign = "+" if n >= 0 else ""
    return f"{sign}{n * 100:.1f}%"


def _is_partial(data: Any) -> bool:
    return isinstance(data, dict) and "__partial__" in data


def _fmt_yi_amount(v: Any) -> str:
    """Format a yuan-denominated amount as 亿元 with 2 decimals (unified unit).

    Used by the three-statement tables so every monetary figure reads in 亿元.
    """
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{n / 1e8:.2f}"


def render_fcf_dividend(data: Any) -> str:
    """Render the FCF / dividend sustainability table.

    Columns: 指标 | 数值 | 解读。数据来自 fcf_dividend 工具。
    """
    if _is_partial(data) or not isinstance(data, dict):
        return ""
    ocf = data.get("ocf")
    capex = data.get("capex")
    fcf = data.get("fcf")
    if ocf is None and capex is None and fcf is None:
        return ""

    cover = data.get("fcf_cover")
    div_yield = data.get("dividend_yield")
    sustain_note = "N/A"
    if cover is not None:
        sustain_note = (
            "可持续(FCF 覆盖分红 ≥1 倍)" if cover >= 1.0
            else f"⚠️ 不可持续:FCF 仅覆盖分红 {cover:.2f} 倍,差额靠存量/筹资填补"
        )
    yield_vs = data.get("yield_vs_cgb")

    lines = [
        "## 自由现金流与股息可持续性",
        "",
        "| 指标 | 数值 | 解读 |",
        "|------|------|------|",
        f"| 经营现金流 OCF | {_fmt_num(ocf)} 亿 | 净利润变现能力 |",
        f"| 资本开支 Capex | {_fmt_num(capex)} 亿 | 投资现金流净流出代理 |",
        f"| 自由现金流 FCF | {_fmt_num(fcf)} 亿 | OCF − Capex |",
        f"| FCF 利润率 | {_pct(data.get('fcf_margin'))} | FCF / 净利润 |",
        f"| 分红总额 | {_fmt_num(data.get('dividend_total'))} 亿 | 取自现金流量表分红科目 |",
        f"| 股息支付率 | {_pct(data.get('payout_ratio'))} | 分红 / 净利润 |",
        f"| 股息率 | {_pct(div_yield)} | 分红总额 / 总市值 |",
        f"| FCF 覆盖分红 | {(f'{cover:.2f} 倍') if cover is not None else 'N/A'} | ≥1 倍方可持续 |",
        f"| 股息率 − 10Y 国债 | {_signed_pct(yield_vs)} | 股债相对价值(国债锚 2.5%) |",
        f"| 分红可持续性 | {sustain_note} | — |",
    ]
    return "\n".join(lines)


def render_financial_health_ext(financial: Any) -> str:
    """Render extended financial-health indicators(应收账款/存货/分红).

    数据来自 FinancialData 实体根级字段(accounts_receivable / inventory /
    dividend_paid),这些字段在旧版实体中不存在,导致「渠道压货」「库存减值」
    「股息可持续性」三大核心判断缺失数据支撑。若全部字段为 0 则返回 ''。
    """
    if not hasattr(financial, "accounts_receivable"):
        return ""
    ar = getattr(financial, "accounts_receivable", 0.0) or 0.0
    inv = getattr(financial, "inventory", 0.0) or 0.0
    div = getattr(financial, "dividend_paid", 0.0) or 0.0
    if ar == 0 and inv == 0 and div == 0:
        return ""
    revenue = getattr(financial, "revenue", 0.0) or 0.0
    net_profit = getattr(financial, "net_profit", 0.0) or 0.0

    # 占营收比(渠道压货/库存积压信号)
    ar_ratio = _pct(ar / revenue) if revenue > 0 else "N/A"
    inv_ratio = _pct(inv / revenue) if revenue > 0 else "N/A"
    # 股利支付率(分红 / 净利润)
    payout = _pct(div / net_profit) if net_profit > 0 and div > 0 else "N/A"

    lines = [
        "## 财务健康扩展指标(应收账款/存货/分红)",
        "",
        "| 指标 | 数值 | 占营收 / 支付率 | 诊断 |",
        "|------|------|----------------|------|",
        f"| 应收账款 | {_fmt_num(ar)} 亿 | {ar_ratio} | 占营收比上升=渠道压货信号 |",
        f"| 存货 | {_fmt_num(inv)} 亿 | {inv_ratio} | 积压风险 / 库存减值先行指标 |",
        f"| 分配股利(现金流出) | {_fmt_num(div)} 亿 | {payout} | 股息可持续性;_coverage=FCF÷分红 |",
    ]
    return "\n".join(lines)


def render_scenario_prob(data: Any) -> str:
    """Render the scenario probability-weighted & risk-reward table.

    Columns: 档位 | 目标价(元) | 目标PE | 概率(%)。来自 scenario_prob 工具。
    """
    if _is_partial(data) or not isinstance(data, dict):
        return ""
    targets = data.get("targets") or {}
    if not isinstance(targets, dict) or not targets:
        return ""

    lines = [
        "## 情景概率加权与风险收益比",
        "",
    ]
    basis = data.get("prob_basis")
    if basis:
        lines.append(f"*赋权依据: {basis}*")
        lines.append("")
    lines.extend([
        "| 档位 | 目标价(元) | 目标PE | 概率(%) |",
        "|------|------------|--------|---------|",
    ])
    for tier in ("conservative", "neutral", "optimistic"):
        t = targets.get(tier)
        if not isinstance(t, dict):
            continue
        name = {"conservative": "保守", "neutral": "中性", "optimistic": "乐观"}.get(tier, tier)
        price = _fmt_num(t.get("price"))
        pe = _fmt_num(t.get("pe"))
        prob = _fmt_num(t.get("probability")) if t.get("probability") is not None else "N/A"
        lines.append(f"| {name} | {price} | {pe} | {prob} |")

    exp = data.get("expected_target")
    exp_pe = data.get("expected_pe")
    if exp is not None:
        lines.append(f"| **加权期望** | **{_fmt_num(exp)}** | **{_fmt_num(exp_pe)}** | **100** |")

    rr = data.get("risk_reward_ratio")
    lines.append("")
    down = data.get("downside_neutral_pct")
    up = data.get("upside_optimistic_pct")
    down_s = f"{down:+.1f}%" if isinstance(down, (int, float)) else "N/A"
    up_s = f"{up:+.1f}%" if isinstance(up, (int, float)) else "N/A"
    lines.append(
        f"*现价 {_fmt_num(data.get('current_price'))} 元: "
        f"中性情景下行空间 **{down_s}**, 乐观情景上行空间 **{up_s}**"
    )
    if rr is not None:
        lines.append(f"*风险收益比(上行/下行非对称): **{rr:.2f}** (＜1 表示下行风险大于上行空间)*")
    else:
        lines.append("*风险收益比: N/A(缺少完整情景目标价或三档均低于现价)*")
    return "\n".join(lines)


def render_sentiment(data: Any) -> str:
    """Render the social-media sentiment analysis table.

    Shows pos/neg/neu split, sentiment index, top keywords & polarity words.
    来自 sentiment 工具。
    """
    if _is_partial(data) or not isinstance(data, dict):
        return ""
    total = data.get("total") or 0
    if not total:
        return ""

    pos = data.get("pos", 0)
    neg = data.get("neg", 0)
    neu = data.get("neu", 0)
    lines = [
        "## 社媒情感分析(量化)",
        "",
        f"*引擎: {data.get('engine', 'N/A')} · 样本 {total} 条 · "
        f"整体情绪: {data.get('label', 'N/A')}(指数 {data.get('sentiment_index', 0)})*",
        "",
        "| 情绪 | 条数 | 占比 |",
        "|------|------|------|",
        f"| 正面 | {pos} | {_pct(data.get('pos_ratio'))} |",
        f"| 负面 | {neg} | {_pct(data.get('neg_ratio'))} |",
        f"| 中性 | {neu} | {_pct(data.get('neu_ratio'))} |",
    ]
    kws = data.get("top_keywords") or []
    if kws:
        kw_str = "、".join(f"{k['word']}({k['count']})" for k in kws[:10])
        lines.append("")
        lines.append(f"**高频词**: {kw_str}")
    pos_words = data.get("pos_words") or []
    neg_words = data.get("neg_words") or []
    if pos_words:
        lines.append(f"**正面词**: {'、'.join(f'{w}({c})' for w, c in pos_words[:6])}")
    if neg_words:
        lines.append(f"**负面词**: {'、'.join(f'{w}({c})' for w, c in neg_words[:6])}")
    return "\n".join(lines)
