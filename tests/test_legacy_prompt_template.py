"""Regression guard for the legacy (v1) user-prompt template.

Y1 fix: `USER_PROMPT_TEMPLATE` previously contained `{financial_summary}` /
`{industry_data}` placeholders that `prompt_builder.build_user_message` never
supplied → `str.format` raised `KeyError`, silently downgrading every
`--legacy` / non-v2 `analyze()` call to "AI 分析暂不可用". The placeholders
were removed; this test locks that regression.
"""

from aimoon.adapters.driven.ai.prompts import USER_PROMPT_TEMPLATE


def test_user_prompt_template_format_no_keyerror():
    """Only the 4 keys build_user_message supplies must be sufficient."""
    out = USER_PROMPT_TEMPLATE.format(
        stock_code="600519",
        stock_name="贵州茅台",
        quote_data="价格 1700 元 涨跌 1.2% PE=30",
        current_time="2026-07-10 10:00:00",
    )
    assert "600519" in out
    assert "贵州茅台" in out
