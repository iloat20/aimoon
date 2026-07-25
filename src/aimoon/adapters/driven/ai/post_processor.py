"""Post-processing of AI analysis output.

Pure functions (no IO): XML-stripping, tail dedup, summary cleanup,
support/resistance sanity, and degradation-notice tagging.
"""

from __future__ import annotations

import json
import re

from aimoon.core.domain.value_objects.analysis_report import AnalysisReport

_INVESTMENT_ADVICE = "本报告由DeepSeek AI自动生成，仅供参考，不构成投资建议。"


def strip_xml_tool_calls(text: str) -> str:
    """Remove XML-style tool call markup from response text."""
    text = re.sub(
        r"<｜｜DSML｜｜tool_calls>.*?</｜｜DSML｜｜tool_calls>", "", text, flags=re.DOTALL
    )
    text = re.sub(r"<｜｜DSML｜｜invoke.*?</｜｜DSML｜｜invoke>", "", text, flags=re.DOTALL)
    return text.strip()


def parse_xml_tool_calls(content: str) -> list[dict]:
    """Parse XML-style tool calls from model content as fallback.

    Handles DeepSeek's ``<｜｜DSML｜｜tool_calls>`` markup format.
    Returns list of ``{name, arguments}`` dicts.
    """
    calls: list[dict] = []
    for m in re.finditer(
        r'<｜｜DSML｜｜invoke\s+name="([^"]+)">(.*?)</｜｜DSML｜｜invoke>',
        content,
        re.DOTALL,
    ):
        fn_name = m.group(1)
        param_block = m.group(2)
        param_m = re.search(
            r'<｜｜DSML｜｜parameter\s+name="query"[^>]*>(.*?)</｜｜DSML｜｜parameter>',
            param_block,
            re.DOTALL,
        )
        query = param_m.group(1).strip() if param_m else ""
        calls.append({"name": fn_name, "arguments": json.dumps({"query": query})})
    return calls


def deduplicate_tail(text: str) -> str:
    """Remove repeated blocks at the end of the response.

    Handles two cases:
    1. Last N paragraphs identical to preceding N paragraphs
    2. Repeated text within the response (e.g., model outputs conclusion twice)
    """
    # Case 1: Deduplicate repeated paragraphs
    paragraphs = re.split(r"\n\n+", text)
    if len(paragraphs) >= 4:
        for size in range(1, len(paragraphs) // 2 + 1):
            candidate = paragraphs[-size:]
            prev = paragraphs[-(2 * size) : -size]
            if [p.strip() for p in candidate] == [p.strip() for p in prev]:
                text = "\n\n".join(paragraphs[: -(size)])
                paragraphs = re.split(r"\n\n+", text)

    # Case 2: Deduplicate repeated text blocks within the response
    # Find repeated substrings of 50+ chars at the end.
    # Cap the scan window to avoid O(n^2) blowup on very long reports.
    max_len = min(len(text) // 3, 400)
    for length in range(max_len, 49, -1):
        tail = text[-length:]
        # Find where this block first appears
        first_pos = text.find(tail)
        tail_start = len(text) - length
        # 只有当首次出现严格早于结尾副本,且两段之间仅剩空白时,才判定为
        # 模型「整段重复输出」并截断。否则正文中偶然重复的 50+ 字片段会被
        # 误当作尾部重复,导致其后所有正文被删除(I7 过度删除)。
        if (
            0 <= first_pos < tail_start
            and text[first_pos + length : tail_start].strip() == ""
        ):
            # Block appears earlier and is repeated at end
            # Remove the duplicate (keep first occurrence)
            return text[: first_pos + length]

    return text


def clean_summary_text(md: str) -> str:
    """Build a <=200 char summary line from the raw markdown report."""
    short = md[:200]
    short = re.sub(r"\*\*(.*?)\*\*", r"\1", short)
    short = re.sub(r"##?\s*", "", short)
    short = re.sub(r"\* ", "• ", short)
    if len(md) > 200:
        short += "..."
    return short


def _extract_first_price(pattern: str, text: str) -> tuple[float | None, re.Match | None]:
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None, None
    price_str = match.group(1).replace(",", "")
    try:
        return float(price_str), match
    except ValueError:
        return None, None


def sanitize_support_resistance(
    report: AnalysisReport, current_price: float | None
) -> AnalysisReport:
    """Support/resistance sanity check.

    If AI gives support >= current price, override to price * 0.92.
    If AI gives resistance <= current price, override to price * 1.08.
    """
    import logging

    if not current_price or current_price <= 0:
        return report

    md = report.report_text
    if not md:
        return report

    support_pattern = r"支撑位[：:\s]\s*([0-9]+(?:\.[0-9]+)?)"
    resistance_pattern = r"阻力位[：:\s]\s*([0-9]+(?:\.[0-9]+)?)"

    support_val, support_match = _extract_first_price(support_pattern, md)
    resistance_val, resistance_match = _extract_first_price(resistance_pattern, md)

    if support_val is None and resistance_val is None:
        return report

    new_md = md

    if support_val is not None and support_val >= current_price:
        safe_support = round(current_price * 0.92, 2)
        if support_match:
            orig = support_match.group(0)
            replacement = orig.replace(support_match.group(1), str(safe_support))
            new_md = (
                new_md[: support_match.start()] + replacement + new_md[support_match.end() :]
            )
            logging.info(
                "[sanity_support] 支撑位 %.2f >= 现价 %.2f，已修正为 %.2f",
                support_val,
                current_price,
                safe_support,
            )

    if resistance_val is not None and resistance_val <= current_price:
        safe_resistance = round(current_price * 1.08, 2)
        if resistance_match:
            orig = resistance_match.group(0)
            replacement = orig.replace(resistance_match.group(1), str(safe_resistance))
            resistance_match_new = re.search(resistance_pattern, new_md, re.IGNORECASE)
            if resistance_match_new:
                new_md = (
                    new_md[: resistance_match_new.start()]
                    + replacement
                    + new_md[resistance_match_new.end() :]
                )
            logging.info(
                "[sanity_resistance] 阻力位 %.2f <= 现价 %.2f，已修正为 %.2f",
                resistance_val,
                current_price,
                safe_resistance,
            )

    if new_md != md:
        report = report.model_copy(update={"report_text": new_md})

    return report


# 中文财经正文里,科学计数法与 9 位以上整数绝不可能合法出现(表格金额已格式化为「亿」)。
# 这类 token 必为模型把「原始元单位数值」(如 171118000000 / 1.71118e+11)错写进正文,
# 属于纯生成错误。清洗是安全的:科学计数法按「元→亿」换算回填,其余直接删。
_NUMERIC_ARTIFACT = re.compile(
    r"(占营收|营收为|营收端为|营收|占比|占)\s*(\d[\d,]{8,}|\d+\.\d+[eE][+-]?\d+)\s*%"
)
_SCI_NUM = re.compile(r"\d+(?:\.\d+)?[eE][+-]?\d+\s*(?:元|%)?")
_BARE_HUGE_PCT = re.compile(r"(?<![\d.,])[\d,]{10,}\s*%")
_STRAY_PCT = re.compile(r"(?<![0-9.])(%)")

# 模型偶尔把同一个营收灾难 token 连写多遍(如
# "营收 X%，营收为 X%，营收为 X%"),经 _fix_revenue_artifact 逐个换算后
# 会得到"营收约 N亿，营收为约 N亿，营收为约 N亿"这种口吃。折叠为首个即可。
# 连接词按长度降序,避免 "营收" 抢先吞掉 "营收为"/"营收端为"。
_REV_PHRASE = r"(?:占营收|营收端为|营收为|营收|占比|占)约\s*\d[\d.]*\s*亿"
_REVENUE_STUTTER = re.compile(r"(" + _REV_PHRASE + r")(?:\s*[，,、]\s*" + _REV_PHRASE + r")+")


def _sci_to_yi(token: str) -> str:
    """把科学计数法 token 换算成「亿」,去掉尾随的 元/% 。"""
    m = re.match(r"(\d+(?:\.\d+)?[eE][+-]?\d+)\s*(元|%)?", token)
    if not m:
        return ""
    try:
        val = float(m.group(1))
    except ValueError:
        return ""
    if abs(val) >= 1e8:
        return f"{val / 1e8:.1f}亿"
    return ""


def _fix_revenue_artifact(m: re.Match) -> str:
    """把「连接词 + 元单位超大数 + %」这类灾难 token 换算回可读的「亿」单位。

    模型常把原始元单位数值(如 1.71118e+11 / 171118000000)错写成
    「占营收 1.7e11%」「营收为 171,118,000,000%」。这些数值在中文财经正文里
    绝不可能合法,但原始数值本身可信(= 元单位营收混入了正文)。

    处理方式:保留连接词,把数值换算成「亿」回填,去掉荒谬的 %,得到自包含、
    与全报告计量单位一致的片段(如「占营收约 1711.2亿」),不再留悬空的
    「(见近年财务时序表)」占位符。
    """
    connector = m.group(1)
    raw = m.group(2).replace(",", "")
    try:
        val = float(raw)
    except ValueError:
        return ""
    if val <= 0:
        return ""
    yi = val / 1e8
    # 汇报粒度:≥100 亿取整,否则 1 位小数,与报告其余金额口径一致。
    yi_str = f"{yi:.0f}亿" if yi >= 100 else f"{yi:.1f}亿"
    return f"{connector}约 {yi_str}"


def sanitize_numeric_artifacts(md: str) -> str:
    """清除模型把原始元单位数值错写进正文的灾难性 token。

    这些 pattern 在中文财经正文里绝不可能合法出现,因此清洗是安全的:
    - 连接词(占营收/营收为/占比/占)+ 科学计数法或 9 位以上整数 + %
      → 连接词保留,数值换算成「亿」回填(如「占营收约 1711.2亿」),
      去掉荒谬的 %,消除悬空占位符。
    - 任何科学计数法数值(元单位,如 1.71118e+11元)→ 按「元→亿」换算回填
      (1.71118e+11 → 1711.2亿),消除 1.7e11 这类灾难 token。
    - 裸「171,118,000,000%」等 10 位以上整数 + % → 直接删除。
    - 没有前置数字的游离 %(模型偶尔把 1.7e11% 拆成「情况下%」)→ 删除。

    仅作用于 AI 正文(md),不影响系统预渲染的数据底稿(已正确格式化)。
    """
    if not md:
        return md
    # 1) 连接词 + 超长/科学计数法数值 + % → 连接词保留,数值换算成「亿」回填
    #    (消除 1.7e11% / 171118000000% 这类灾难 token,不再留悬空占位符)
    md = _NUMERIC_ARTIFACT.sub(_fix_revenue_artifact, md)
    # 1.5) 折叠模型连写导致的营收 token 口吃(仅保留首个),避免
    #    "营收约 N亿，营收为约 N亿，营收为约 N亿"这种重复。
    md = _REVENUE_STUTTER.sub(r"\1", md)
    # 2) 科学计数法数值(元单位)→ 换算成「亿」回填
    md = _SCI_NUM.sub(lambda m: _sci_to_yi(m.group(0)), md)
    # 3) 裸 10 位以上整数 + % → 直接删除
    md = _BARE_HUGE_PCT.sub("", md)
    # 4) 没有前置数字的游离 % → 删除
    md = _STRAY_PCT.sub("", md)
    return md


def _fix_net_cash_pe(md: str, appendix_md: str) -> str:
    """纠正正文把「净现金调整 PE」误写成 PE(TTM) 值的情况。

    模型常把 净现金调整 PE(权威表 = (市值−货币资金)/净利润, 恒 < PE(TTM))
    误写成 PE(TTM) 的数值(如把 4.04 写成 7.79),造成"剔除现金后 PE 更低"的
    论证自相矛盾。以系统预渲染的「估值安全边际表」为权威真值,仅当正文数值
    恰好等于 PE(TTM)(即典型的两者混淆)时才纠正,绝不改写压力情景等其他数值。
    """
    if not md or not appendix_md:
        return md
    m_nc = re.search(r"净现金调整\s*PE\s*[|｜]\s*([0-9]+(?:\.[0-9]+)?)", appendix_md)
    m_pe = re.search(r"PE\s*\(\s*TTM\s*\)\s*[|｜]\s*([0-9]+(?:\.[0-9]+)?)", appendix_md)
    if not m_nc or not m_pe:
        return md
    auth, pe_ttm = m_nc.group(1), m_pe.group(1)
    try:
        auth_f, pe_f = float(auth), float(pe_ttm)
    except ValueError:
        return md
    if abs(auth_f - pe_f) < 1e-9:
        return md  # 两者相等则无从判别,不动

    def _same_as_pe(num: str) -> bool:
        try:
            return abs(float(num) - pe_f) < 1e-6
        except ValueError:
            return False

    # 正序: 净现金调整 PE [约] N [倍] —— 仅当 N == PE(TTM) 时替换为权威值
    def _fwd(mm: re.Match) -> str:
        num, suffix = mm.group(1), mm.group(2) or ""
        return f"净现金调整 PE {auth}{suffix}" if _same_as_pe(num) else mm.group(0)

    md = re.sub(
        r"净现金调整\s*PE\s*(?:约\s*)?([0-9]+(?:\.[0-9]+)?)(\s*倍)?", _fwd, md
    )

    # 反序: N倍净现金调整 PE
    def _rev(mm: re.Match) -> str:
        return f"{auth}倍净现金调整 PE" if _same_as_pe(mm.group(1)) else mm.group(0)

    md = re.sub(r"([0-9]+(?:\.[0-9]+)?)\s*倍净现金调整\s*PE", _rev, md)
    return md


def _fix_capex(md: str, appendix_md: str) -> str:
    """纠正正文把「资本开支 Capex」误写成 PE(TTM) 值的情况(如把 17.2 亿写成 7.79 亿)。

    模型偶发把 PE(TTM) 数值(如 7.79)错抄为 Capex(资本开支),
    造成「资本开支仅 7.79 亿」与财务健康扩展表 Capex≈17 亿自相矛盾。
    以系统预渲染的「财务健康扩展表」权威 Capex 为真值,仅当正文 Capex 数值
    恰好等于 PE(TTM)(典型的两者混淆)时才纠正,绝不改写其他数值/其他单位。
    """
    if not md or not appendix_md:
        return md
    # 权威 Capex 来自「财务健康扩展表」: `| 资本开支 Capex | 17.2 亿 | ... |`
    m_capex = re.search(
        r"资本开支\s*Capex\s*[|｜]\s*([0-9]+(?:\.[0-9]+)?)\s*亿", appendix_md
    )
    if not m_capex:
        return md
    auth_capex = m_capex.group(1)
    try:
        auth_f = float(auth_capex)
    except ValueError:
        return md
    if auth_f == 0:
        return md
    # PE(TTM) 来自「估值安全边际表」: `| 当前 PE(TTM) | 7.79 | ... |`
    m_pe = re.search(r"PE\s*\(\s*TTM\s*\)\s*[|｜]\s*([0-9]+(?:\.[0-9]+)?)", appendix_md)
    if not m_pe:
        return md
    pe_ttm = m_pe.group(1)
    try:
        pe_f = float(pe_ttm)
    except ValueError:
        return md
    if abs(auth_f - pe_f) < 1e-9:
        return md  # Capex 与 PE 相等则无从判别,不动

    def _same_as_pe(num: str) -> bool:
        try:
            return abs(float(num) - pe_f) < 1e-6
        except ValueError:
            return False

    # 匹配「Capex N 亿」或「资本开支 Capex N 亿」(中英文混排,大小写不敏感),
    # 仅当 N 恰好等于 PE(TTM)(= 典型的两者混淆)时替换为权威 Capex。
    def _rep(mm: re.Match) -> str:
        prefix, num = mm.group(1), mm.group(2)
        return f"{prefix} {auth_capex} 亿" if _same_as_pe(num) else mm.group(0)

    md = re.sub(
        r"((?:资本开支\s*)?Capex)\s*([0-9]+(?:\.[0-9]+)?)\s*亿",
        _rep,
        md,
        flags=re.IGNORECASE,
    )
    return md


def build_analysis_report(
    *,
    symbol: str,
    name: str,
    md: str,
    current_price: float | None = None,
    data_appendix_md: str = "",
    margin_of_safety_html: str = "",
) -> AnalysisReport:
    """Assemble a finalized AnalysisReport from raw markdown output.

    Applies the same summary-cleanup + support/resistance sanity path used by
    both the legacy and v2 analyzers.
    """
    md = sanitize_numeric_artifacts(md)
    md = _fix_net_cash_pe(md, data_appendix_md)
    md = _fix_capex(md, data_appendix_md)
    short = clean_summary_text(md)
    result = AnalysisReport(
        symbol=symbol,
        name=name,
        summary=short,
        report_text=md,
        investment_advice=_INVESTMENT_ADVICE,
        data_appendix_md=data_appendix_md,
        margin_of_safety_html=margin_of_safety_html,
    )
    if current_price is not None:
        result = sanitize_support_resistance(result, current_price)
    return result


def with_degradation_notice(report: AnalysisReport, notice: str) -> AnalysisReport:
    """Return a new Report with a visible degradation marker appended (immutable)."""
    marker = f"\n\n<!-- 降级标记: {notice} -->"
    text = report.report_text
    if "<!-- 降级标记:" not in text:
        text = text + marker
    return report.model_copy(update={"report_text": text})
