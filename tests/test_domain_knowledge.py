"""TDD 测试：A 股领域知识包可被加载且覆盖关键主题。"""

from __future__ import annotations

from aimoon.adapters.driven.ai.pipeline.prompts import load_prompt

DOMAIN_KNOWLEDGE_FILE = "domain_knowledge.md"


def test_domain_knowledge_loads_and_contains_key_themes() -> None:
    text = load_prompt(DOMAIN_KNOWLEDGE_FILE)

    # 文件必须存在且被加载
    assert text, "domain_knowledge.md 未加载到内容"

    # 关键主题必须覆盖
    assert "北向" in text, "缺少北向资金相关约束"
    assert "涨跌停" in text, "缺少涨跌停规则"

    # 内容需具实质（空话无法通过）
    assert len(text) > 500, f"领域知识包过短（{len(text)} 字符），缺乏实质内容"
