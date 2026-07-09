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
    # Find repeated substrings of 50+ chars at the end
    for length in range(len(text) // 3, 50, -1):
        tail = text[-length:]
        # Find where this block first appears
        first_pos = text.find(tail)
        if first_pos >= 0 and first_pos + length < len(text):
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


def build_analysis_report(
    *,
    symbol: str,
    name: str,
    md: str,
    current_price: float | None = None,
) -> AnalysisReport:
    """Assemble a finalized AnalysisReport from raw markdown output.

    Applies the same summary-cleanup + support/resistance sanity path used by
    both the legacy and v2 analyzers.
    """
    short = clean_summary_text(md)
    result = AnalysisReport(
        symbol=symbol,
        name=name,
        summary=short,
        report_text=md,
        investment_advice=_INVESTMENT_ADVICE,
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
