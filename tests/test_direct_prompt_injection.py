"""Task 7 — DIRECT 系统提示注入领域知识包 + 引用纪律 (TDD).

测试对象: 真实函数 ``aimoon.adapters.driven.ai.pipeline.phases.phase_system_prompt``,
它是 DIRECT 系统提示的实际组装点。orchestrator 的 ``_phase_direct`` 在
``system = phase_system_prompt(Phase.DIRECT, stock_md, {})`` (orchestrator.py:469)
处调用它拿到最终 system prompt。

我们直接调用该真实函数,并断言返回的最终 system prompt 文本同时包含:
  - 领域知识包的关键内容("北向", 来自 ``domain_knowledge.md``)
  - direct.md 自身的引用纪律约束("引用")
"""

from aimoon.adapters.driven.ai.pipeline import Phase
from aimoon.adapters.driven.ai.pipeline.phases import phase_system_prompt


def test_direct_prompt_includes_knowledge_and_citation_rule():
    """最终 DIRECT system prompt 必须同时含领域知识包与引用纪律。"""
    system = phase_system_prompt(Phase.DIRECT, "", {})
    # 领域知识包(domain_knowledge.md)被注入
    assert "北向" in system
    # direct.md 的引用纪律段被纳入
    assert "引用" in system
