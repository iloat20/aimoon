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


def sanitize_numeric_artifacts(md: str) -> str:
    """清除模型把原始元单位数值错写进正文的灾难性 token。

    这些 pattern 在中文财经正文里绝不可能合法出现,因此清洗是安全的:
    - 连接词(占营收/营收为/占比/占)+ 科学计数法或 9 位以上整数 + %
      → 连接词保留,替换为「(见近年财务时序表)」避免悬空。
    - 任何科学计数法数值(元单位,如 1.71118e+11元)→ 按「元→亿」换算回填
      (1.71118e+11 → 1711.2亿),消除 1.7e11 这类灾难 token。
    - 裸「171,118,000,000%」等 10 位以上整数 + % → 直接删除。
    - 没有前置数字的游离 %(模型偶尔把 1.7e11% 拆成「情况下%」)→ 删除。

    仅作用于 AI 正文(md),不影响系统预渲染的数据底稿(已正确格式化)。
    """
    if not md:
        return md
    # 1) 连接词 + 超长/科学计数法数值 + % → 连接词保留,替换为「(见近年财务时序表)」
    md = _NUMERIC_ARTIFACT.sub(r"\1（见近年财务时序表）", md)
    # 2) 科学计数法数值(元单位)→ 换算成「亿」回填
    md = _SCI_NUM.sub(lambda m: _sci_to_yi(m.group(0)), md)
    # 3) 裸 10 位以上整数 + % → 直接删除
    md = _BARE_HUGE_PCT.sub("", md)
    # 4) 没有前置数字的游离 % → 删除
    md = _STRAY_PCT.sub("", md)
    return md


def build_analysis_report(
    *,
    symbol: str,
    name: str,
    md: str,
    current_price: float | None = None,
    data_appendix_md: str = "",
) -> AnalysisReport:
    """Assemble a finalized AnalysisReport from raw markdown output.

    Applies the same summary-cleanup + support/resistance sanity path used by
    both the legacy and v2 analyzers.
    """
    md = sanitize_numeric_artifacts(md)
    short = clean_summary_text(md)
    result = AnalysisReport(
        symbol=symbol,
        name=name,
        summary=short,
        report_text=md,
        investment_advice=_INVESTMENT_ADVICE,
        data_appendix_md=data_appendix_md,
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
