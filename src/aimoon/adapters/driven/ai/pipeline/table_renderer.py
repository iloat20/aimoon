"""Render tool JSON results into Markdown tables (zero LLM tokens).

Core tables:
1. Financial timeline (from financial_temporal.years)
2. Peer comparison (from peer_compare.peers)
3. Valuation safety margin (from margin_of_safety: net_cash_pe / peer_pe_median / stress)
"""

from __future__ import annotations

import statistics
from typing import Any

COLUMNS = (
    "报告期 | 营收(亿) | 营收同比(%) | 净利润(亿) | 净利同比(%) | ROE(%) | EPS"
    " | 经营现金流(亿) | 自由现金流(亿)"
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
        "|--------|----------|-------------|------------|-------------|--------|-----|----------------|----------------|",
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
        fcf = _fmt_num(y.get("fcf"))
        lines.append(
            f"| {period} | {rev} | {rev_yoy} | {np_} | {np_yoy} | {roe} | {eps} | {ocf} | {fcf} |"
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
    ]
    # 数据异常自检:同行 PE 与标的串号/解析污染时,前置告警并阻止据此做估值结论。
    if data.get("data_quality") == "anomaly":
        msg = str(data.get("anomaly_msg") or "同行对比数据异常,分析失效")
        lines.append(f"> ⚠️ **同行数据异常**:{msg}。")
        lines.append("")

    lines.extend(
        [
            "| 公司 | 最新价 | PE | PB | ROE(%) | 营收增速(%) | 净利增速(%) | 市值(亿) |",
            "|------|--------|----|----|--------|------------|------------|----------|",
        ]
    )
    for p in peers:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "N/A")
        price = _fmt_num(p.get("price") or p.get("latest_price"))
        pe = _fmt_num(p.get("pe"))
        pb = _fmt_num(p.get("pb"))
        # 同行 ROE 当前未采集(peer_compare 仅取 PE/PB/市值),渲染为"—"避免误导为 0。
        _roe_raw = p.get("roe")
        roe = "—" if not _roe_raw else _fmt_pct(_roe_raw)
        rev_g = _fmt_pct(p.get("rev_g") or p.get("revenue_growth"))
        np_g = _fmt_pct(p.get("np_g") or p.get("profit_growth"))
        mcap = _fmt_num(p.get("mcap") or p.get("market_cap"))
        lines.append(
            f"| {name} | {price} | {pe} | {pb} | {roe} | {rev_g} | {np_g} | {mcap} |"
        )

    # 行业中位数(PE/PB):供估值规则对照,消 8.1「同行业 PE/PB 中位数」缺失。
    pes = [
        float(p.get("pe") or 0.0)
        for p in peers
        if isinstance(p, dict) and p.get("pe") not in (None, 0.0, 0)
    ]
    pbs = [
        float(p.get("pb") or 0.0)
        for p in peers
        if isinstance(p, dict) and p.get("pb") not in (None, 0.0, 0)
    ]
    if pes or pbs:
        median_pe = statistics.median(pes) if pes else None
        median_pb = statistics.median(pbs) if pbs else None
        lines.append(
            f"| **行业中位数** | - | {_fmt_num(median_pe)} | "
            f"{_fmt_num(median_pb)} | - | - | - | - |"
        )
    # 同行 ROE 由 peer_compare 未采集(仅 PE/PB/市值),统一以"—"呈现,避免误读为 0。
    lines.append("")
    lines.append('> 注:同行 ROE 未采集,以"—"表示;PE/PB/市值来自行情接口(新浪→腾讯)。')
    return "\n".join(lines)


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


def render_margin_of_safety(data: Any) -> str:
    """渲染估值安全边际表(确定性计算,无目标价)。

    列: 指标 | 数值 | 解读。数据来自 margin_of_safety 工具(原 valuation 工具瘦身):
    当前 PE/PB、净现金调整 PE、同业 PE 中位数、确定性压力测试(净利 -30%/-50%
    → EPS → 股价 → 下行空间)。AI 直接引用,严禁重算。
    """
    if _is_partial(data) or not isinstance(data, dict):
        return ""
    pe = data.get("pe")
    pb = data.get("pb")
    # 工具对缺失 PE/PB 发射 0.0 哨兵,非真实 0;pe/pb 任一为正才算"有行情"。
    pe_ok = isinstance(pe, (int, float)) and pe > 0
    pb_ok = isinstance(pb, (int, float)) and pb > 0
    if not pe_ok and not pb_ok:
        return ""
    pe_disp = _fmt_num(pe) if pe_ok else "N/A"
    pb_disp = _fmt_num(pb) if pb_ok else "N/A"

    net_cash_pe = data.get("net_cash_pe")
    peer_median = data.get("peer_pe_median")
    stress = data.get("stress") or []

    lines = [
        "## 估值安全边际",
        "",
        "| 指标 | 数值 | 解读 |",
        "|------|------|------|",
        f"| 当前 PE(TTM) | {pe_disp} | 行情派生 |",
        f"| 当前 PB | {pb_disp} | 行情派生 |",
        f"| 净现金调整 PE | {_fmt_num(net_cash_pe)} | (市值−货币资金)/净利润,剔除现金安全垫 |",
        f"| 同业 PE 中位数 | {_fmt_num(peer_median)} | 来自 Peer 表 |",
    ]
    if stress:
        lines.append("")
        lines.append("**确定性压力测试**(恒定 PE,净利下滑 → 股价同比例下滑):")
        lines.append("")
        lines.append("| 情景 | 压力净利(亿) | 压力 EPS | 压力股价(元) | 下行空间 |")
        lines.append("|------|--------------|----------|--------------|----------|")
        for s in stress:
            drop = s.get("drop")
            np_ = s.get("net_profit")
            eps = s.get("eps")
            price = s.get("price")
            down = s.get("downside_pct")
            name = f"净利 −{drop:.0f}%" if isinstance(drop, (int, float)) else "压力"
            down_s = f"{down:+.1f}%" if isinstance(down, (int, float)) else "N/A"
            lines.append(
                f"| {name} | {_fmt_num(np_)} | {_fmt_num(eps)} | {_fmt_num(price)} | {down_s} |"
            )
    return "\n".join(lines)


def render_financial_health_ext(
    financial: Any, fin: Any = None
) -> str:
    """Render extended financial-health indicators(资产负债/现金/应收存货/分红).

    数据来自 FinancialData 实体根级字段,含确定性扩展字段
    (monetary_funds / construction_in_progress / capex / 资产负债率派生)。
    若全部字段为 0 则返回 ''。

    ``fin`` 为「自由现金流与股息」工具结果(fcf),其直接携带 ``capex`` 字段,
    与自由现金流表同源。根级 ``financial.capex`` 缺失(=0)时优先回退到此值,
    避免「财务健康表 Capex=0」与「FCF 表 Capex=486亿」自相矛盾;
    若 fcf 也未提供 capex,再回退到 financial_temporal 最新年 capex。
    """
    if not hasattr(financial, "accounts_receivable"):
        return ""
    ar = getattr(financial, "accounts_receivable", 0.0) or 0.0
    inv = getattr(financial, "inventory", 0.0) or 0.0
    div = getattr(financial, "dividend_paid", 0.0) or 0.0
    mf = getattr(financial, "monetary_funds", 0.0) or 0.0
    cip = getattr(financial, "construction_in_progress", 0.0) or 0.0
    capex = getattr(financial, "capex", 0.0) or 0.0
    # 回退:根级 capex 缺失时,优先用「自由现金流与股息」表同源 capex(fcf 直接含),
    # 其次用 financial_temporal 最新年 capex,保证与自由现金流表口径一致。
    if capex == 0 and isinstance(fin, dict):
        fcf_capex = fin.get("capex")
        if fcf_capex:
            capex = float(fcf_capex)
        else:
            years = fin.get("years") or []
            if years and isinstance(years[0], dict) and years[0].get("capex"):
                capex = float(years[0]["capex"])
    ta = getattr(financial, "total_assets", 0.0) or 0.0
    tl = getattr(financial, "total_liabilities", 0.0) or 0.0
    eq = getattr(financial, "equity", 0.0) or 0.0
    if ar == 0 and inv == 0 and div == 0 and mf == 0 and cip == 0 and capex == 0:
        return ""
    revenue = getattr(financial, "revenue", 0.0) or 0.0
    net_profit = getattr(financial, "net_profit", 0.0) or 0.0

    # 占营收比(渠道压货/库存积压信号)
    ar_ratio = _pct(ar / revenue) if revenue > 0 else "N/A"
    inv_ratio = _pct(inv / revenue) if revenue > 0 else "N/A"
    # 股利支付率(分红 / 净利润)
    payout = _pct(div / net_profit) if net_profit > 0 and div > 0 else "N/A"
    # 杜邦拆解派生:资产负债率 / 权益乘数
    debt_ratio = _pct(tl / ta) if ta > 0 else "N/A"
    equity_mult = _fmt_num(ta / eq) if eq > 0 else "N/A"
    # 现金/资本开支占比信号
    mf_ratio = _pct(mf / ta) if ta > 0 else "N/A"
    cip_ratio = _pct(cip / ta) if ta > 0 else "N/A"
    capex_ratio = _pct(capex / revenue) if revenue > 0 else "N/A"

    lines = [
        "## 财务健康扩展指标(资产负债/现金/应收存货/分红)",
        "",
        "| 指标 | 数值 | 占资产 / 营收 | 诊断 |",
        "|------|------|----------------|------|",
        f"| 资产负债率 | {debt_ratio} | 负债/总资产 | 杜邦拆解杠杆依赖度 |",
        f"| 权益乘数 | {equity_mult} | 总资产/权益 | >2 = 高杠杆维持 ROE,下行受损更重 |",
        f"| 货币资金 | {_fmt_num(mf)} 亿 | {mf_ratio} | 短期分红能力 / 真实财务弹性 |",
        f"| 在建工程 | {_fmt_num(cip)} 亿 | {cip_ratio} | 战略性资本开支信号 |",
        f"| 资本开支 Capex | {_fmt_num(capex)} 亿 | {capex_ratio} | "
        f"购建固定资产(真实 capex,区别于理财) |",
        f"| 应收账款 | {_fmt_num(ar)} 亿 | {ar_ratio} | 占营收比上升=渠道压货信号 |",
        f"| 存货 | {_fmt_num(inv)} 亿 | {inv_ratio} | 积压风险 / 库存减值先行指标 |",
        f"| 分配股利(现金流出) | {_fmt_num(div)} 亿 | {payout} | 股息可持续性;_coverage=FCF÷分红 |",
    ]
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


def render_segment_revenue(financial: Any) -> str:
    """渲染分业务营收(按产品分类,最新年报),消 8.1 缺失清单 #3。

    数据来自 FinancialData.segment_revenue(确定性采集自东财 F10 主营构成),
    非空时渲染,供 LLM 在正文引用「见分业务表」,而非标数据缺失。
    """
    if not hasattr(financial, "segment_revenue"):
        return ""
    segs = getattr(financial, "segment_revenue", []) or []
    if not isinstance(segs, list) or not segs:
        return ""
    rows = [s for s in segs if isinstance(s, dict) and s.get("name") and s.get("revenue_yi")]
    if not rows:
        return ""

    lines = [
        "## 分业务营收(按产品分类,最新年报)",
        "",
        "| 业务名称 | 营业收入(亿) | 收入占比(%) | 毛利率(%) |",
        "|----------|--------------|--------------|------------|",
    ]
    for s in rows:
        name = str(s.get("name", "N/A"))
        rev = _fmt_num(s.get("revenue_yi"))
        # ratio / gross_margin 存储为小数(0.78),用 _pct 转百分比(78.1%)
        ratio = _pct(s.get("ratio")) if s.get("ratio") not in (None, 0.0, 0) else "N/A"
        margin = (
            _pct(s.get("gross_margin"))
            if s.get("gross_margin") not in (None, 0.0, 0)
            else "N/A"
        )
        lines.append(f"| {name} | {rev} | {ratio} | {margin} |")
    return "\n".join(lines)


def render_annual_report_footnotes(financial: Any) -> str:
    """渲染年报附注摘录(应收账款保理/证券化/账龄,存货跌价准备,关联交易,应付账款账龄)。

    数据来自 FinancialData.annual_report_footnotes(确定性采集自巨潮年报 PDF,
    annual_report_pdf.parse_footnotes_from_text)。available=True 且 excerpts 非空时渲染,
    供 LLM 在法务会计/估值正文引用「见年报附注表」,而非把上述项列入 8.1 缺失清单。
    """
    if not hasattr(financial, "annual_report_footnotes"):
        return ""
    fn = getattr(financial, "annual_report_footnotes", {}) or {}
    if not isinstance(fn, dict):
        return ""
    if not fn.get("available") or not fn.get("excerpts"):
        return ""
    excerpts = [
        e
        for e in fn["excerpts"]
        if isinstance(e, dict) and e.get("topic") and e.get("text")
    ]
    if not excerpts:
        return ""
    title = str(fn.get("report_title") or "年报")
    lines = [
        f"## 年报附注摘录(来源: {title})",
        "",
        "> 以下为年报原文摘录,供法务会计/估值章节直接引用,勿重复标「数据缺失」。",
        "",
    ]
    for e in excerpts:
        lines.append(f"**{e['topic']}**: {e['text']}")
        lines.append("")
    return "\n".join(lines)


def render_quarterly_breakdown(financial: Any) -> str:
    """渲染分季度主要财务指标(单季营收/归母净利 + 单季同比),消 8.1 缺失清单 #1。

    数据来自 FinancialData.annual_report_footnotes["quarterly_breakdown"](确定性采集自
    巨潮年报 PDF「八、分季度主要财务指标」)。非空时渲染,供 LLM 在正文引用「见分季度表」,
    而非把分季度营收/净利列入 8.1 缺失清单。同比由上年同期年报合并,缺失则显示 N/A。
    """
    if not hasattr(financial, "annual_report_footnotes"):
        return ""
    fn = getattr(financial, "annual_report_footnotes", {}) or {}
    if not isinstance(fn, dict):
        return ""
    qb = fn.get("quarterly_breakdown")
    if not isinstance(qb, dict) or not qb.get("available") or not qb.get("quarters"):
        return ""
    quarters = qb["quarters"]
    lines = [
        "## 分季度主要财务指标(单季值,来源: 巨潮年报 PDF)",
        "",
        "| 季度 | 营业收入(亿) | 营收同比(%) | 归母净利润(亿) | 净利同比(%) |",
        "|------|--------------|--------------|----------------|--------------|",
    ]
    for q in quarters:
        rev = _fmt_num(q.get("revenue_yi"))
        rev_yoy = _fmt_pct(q.get("revenue_yoy"))
        np_ = _fmt_num(q.get("net_profit_yi"))
        np_yoy = _fmt_pct(q.get("net_profit_yoy"))
        lines.append(f"| {q.get('quarter')} | {rev} | {rev_yoy} | {np_} | {np_yoy} |")
    return "\n".join(lines)


def render_region_breakdown(financial: Any) -> str:
    """渲染主营业务分地区(内销/外销),消 8.1 缺失清单 #2。

    数据来自 FinancialData.annual_report_footnotes["region_breakdown"](确定性采集自
    巨潮年报 PDF「营业收入构成-分地区」)。非空时渲染,供 LLM 引用「见分地区表」。
    注: 这是公司主营业务层面拆分,非空调业务专属(空调专属内销/出口仅第三方可得,
    见 8.1),LLM 可将其作为空调内外销的强代理但不应声称是空调专属值。
    """
    if not hasattr(financial, "annual_report_footnotes"):
        return ""
    fn = getattr(financial, "annual_report_footnotes", {}) or {}
    if not isinstance(fn, dict):
        return ""
    rb = fn.get("region_breakdown")
    if not isinstance(rb, dict) or not rb.get("available") or not rb.get("regions"):
        return ""
    regions = rb["regions"]
    lines = [
        "## 主营业务分地区(内销/外销,来源: 巨潮年报 PDF)",
        "",
        "| 地区 | 营业收入(亿) | 收入占比(%) | 营收同比(%) | 毛利率(%) |",
        "|------|--------------|--------------|--------------|------------|",
    ]
    for r in regions:
        name = str(r.get("name", "N/A"))
        rev = _fmt_num(r.get("revenue_yi"))
        ratio = _fmt_pct(r.get("ratio"))
        yoy = _fmt_pct(r.get("yoy"))
        margin = _fmt_pct(r.get("gross_margin"))
        lines.append(f"| {name} | {rev} | {ratio} | {yoy} | {margin} |")
    lines.append("")
    lines.append(
        "> 注: 上述为**公司主营业务**层面内销/外销拆分(年报「分地区」),非空调业务专属。"
        "空调专属内销/出口无公开确定性来源(仅产业在线等第三方),详见 8.1 关键缺失数据清单。"
    )
    return "\n".join(lines)


def render_channel_proxy(financial: Any) -> str:
    """渲染渠道代理指标:合同负债(经销商预收打款蓄水池),消 8.1 缺失清单 #3(代理)。

    经销商数量 / 真实渠道库存无公开确定性来源;**合同负债**(经销商提前打款待提货)
    是市场公认的渠道需求与压货节奏**代理指标**,确定性可采(资产负债表科目)。
    非空(>0)时渲染,供 LLM 在渠道改革/压货讨论引用「见渠道代理指标表」,
    并把【经销商数量/渠道库存】从 8.1 缺口降级为「代理指标已给出,真实值仅第三方可得」。
    """
    if not hasattr(financial, "contract_liabilities"):
        return ""
    cl = getattr(financial, "contract_liabilities", 0.0) or 0.0
    if cl == 0:
        return ""
    cl_prev = getattr(financial, "contract_liabilities_prev", 0.0) or 0.0
    revenue = getattr(financial, "revenue", 0.0) or 0.0

    yoy = "N/A"
    if cl_prev > 0:
        yoy = _signed_pct((cl - cl_prev) / cl_prev)
    ratio = _pct(cl / revenue) if revenue > 0 else "N/A"

    if cl_prev > 0:
        if cl > cl_prev:
            diag = "合同负债同比上升 = 经销商备货积极 / 渠道压货节奏加速"
        else:
            diag = "合同负债同比下降 = 渠道主动去库存 / 提货放缓"
    else:
        diag = "无上年同期对比(东财仅软封历史时缺),仅看绝对蓄水池"

    prev_s = _fmt_num(cl_prev) if cl_prev > 0 else "N/A"
    lines = [
        "## 渠道代理指标:合同负债(经销商打款蓄水池)",
        "",
        "> 经销商数量 / 真实渠道库存无公开确定性来源;**合同负债**(经销商提前打款待提货)"
        "是市场公认的渠道需求与压货节奏**代理指标**,确定性可采(资产负债表科目)。"
        "以下为**代理值**,非真实经销商家数 / 渠道库存吨数。",
        "",
        "| 指标 | 数值 | 解读 |",
        "|------|------|------|",
        f"| 合同负债(最新年报) | {_fmt_num(cl)} 亿 | 经销商预收待发货,渠道蓄水池 |",
        f"| 合同负债(上一年) | {prev_s} 亿 | 同比对照基数 |",
        f"| 同比 | {yoy} | 上升=压货/备货积极,下降=去库存 |",
        f"| 占营收比 | {ratio} | 季节性蓄水代理;异常偏高=压货风险 |",
        f"| 渠道诊断 | {diag} | 替代『经销商数量/渠道库存』缺失项(代理) |",
    ]
    return "\n".join(lines)
