"""self_check_rewrite 的定点重写测试。"""

from aimoon.adapters.driven.ai.pipeline.report_reconciler import Mismatch
from aimoon.adapters.driven.ai.pipeline.self_check_rewrite import self_check_rewrite


def _fake_llm_returning(sentence: str):
    def _f(system: str, user: str) -> str:
        return sentence

    return _f


def test_rewrite_replaces_wrong_sentence():
    report = "该股 PE 为 99.9，明显高估。"
    mm = Mismatch(
        snippet="该股 PE 为 99.9，明显高估。",
        claimed="99.9",
        expected="21.3",
        metric="pe_ttm",
        severity="critical",
    )
    fixed = self_check_rewrite(
        report,
        [mm],
        {"pe_ttm": 21.3},
        llm=_fake_llm_returning("该股 PE 为 21.3（见基本面表），估值中性。"),
    )
    assert "99.9" not in fixed
    assert "21.3" in fixed


def test_rewrite_keeps_original_when_not_replaceable():
    report = "该股 PE 为 99.9，明显高估。"
    mm = Mismatch(
        snippet="该股 PE 为 99.9，明显高估。",
        claimed="99.9",
        expected="21.3",
        metric="pe_ttm",
        severity="critical",
    )
    # LLM 返回一句不在原文中的话 → 不应替换
    fixed = self_check_rewrite(
        report,
        [mm],
        {"pe_ttm": 21.3},
        llm=_fake_llm_returning("完全不同的句子。"),
    )
    assert "99.9" in fixed
