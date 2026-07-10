"""质量护栏端到端集成测试（TDD）。

用 fake llm 走 ``_verify_and_fix`` 的 0-LLM 数字对账 + LLM 定点重写护栏，
验证三条核心行为：假数字被改正、清白报告不变、脏输入不崩。不走真实网络。
"""

from __future__ import annotations

from aimoon.adapters.driven.ai.pipeline.orchestrator import _verify_and_fix
from aimoon.adapters.driven.config.settings import get_settings

# 质量护栏依赖两个开关，默认开启；但 .env 可能覆盖，测试内显式开启保证确定性。
_settings = get_settings()
_settings.reconcile_enabled = True
_settings.self_check_rewrite_enabled = True


def _fake_llm_returning(sentence: str):
    def _f(system: str, user: str) -> str:
        return sentence

    return _f


def test_end_to_end_quality_guardrail() -> None:
    # mock DIRECT 输出含一个已知假数字 → 对账抓 → 自检改正 → 最终与 facts 一致
    facts = {"pe_ttm": 21.3}
    fake_direct = "PE 为 99.9（见基本面表），高估。"
    out, summary = _verify_and_fix(
        fake_direct, facts, llm=_fake_llm_returning("PE 为 21.3（见基本面表），中性。")
    )
    assert "99.9" not in out and "21.3" in out
    assert summary["corrected"] >= 1


def test_clean_report_passes_through_unchanged() -> None:
    facts = {"pe_ttm": 21.3}
    clean = "PE 为 21.3（见基本面表），估值中性。"
    out, summary = _verify_and_fix(clean, facts, llm=_fake_llm_returning("x"))
    assert out == clean
    assert summary["corrected"] == 0
    assert summary["uncertain"] == []


def test_garbage_input_no_crash() -> None:
    out, summary = _verify_and_fix("### 乱码 @#$%", {}, llm=lambda s, u: "x")
    assert "乱码" in out
