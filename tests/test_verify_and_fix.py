from aimoon.adapters.driven.ai.pipeline._helpers import _verify_and_fix


def _fake_llm_returning(sentence):
    def _f(system, user):
        return sentence

    return _f


def test_verify_and_fix_corrects_fake_number():
    report = "PE 为 99.9（见基本面表）。"
    facts = {"pe_ttm": 21.3}
    out, summary = _verify_and_fix(
        report, facts, llm=_fake_llm_returning("PE 为 21.3（见基本面表）。")
    )
    assert "99.9" not in out
    assert "21.3" in out
    assert summary["corrected"] >= 1


def test_verify_and_fix_never_crashes():
    out, summary = _verify_and_fix("乱码 @#$%", {}, llm=lambda s, u: "x")
    assert "乱码" in out  # 原样返回，不崩


def test_critical_fiction_flagged_uncertain_not_rewritten():
    """虚构指标(critical)不应被 LLM 重写，应标记 uncertain 让用户可见。"""
    report = "目标价 999 元，空间巨大。"  # target_base absent in facts => critical
    facts = {"pe_ttm": 21.3}
    out, summary = _verify_and_fix(
        report, facts, llm=_fake_llm_returning("目标价 50 元，空间巨大。")
    )
    assert "999" in out  # 虚构指标不被重写
    assert summary.get("uncertain")  # 标记 uncertain 让用户可见
