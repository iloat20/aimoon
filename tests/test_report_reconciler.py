"""TDD 失败测试：0-LLM 数字对账核心 reconcile。"""

from aimoon.adapters.driven.ai.pipeline.report_reconciler import reconcile


def test_fabricated_metric_flagged():
    # facts 中无 "pb"，却断言了市净率 => 虚构指标 => critical
    facts = {"pe_ttm": 21.3, "price": 1685.0}
    report = "该股 PB 为 3.5，明显高估。"
    res = reconcile(report, facts)
    assert any(m.severity == "critical" for m in res.mismatches)


def test_value_mismatch_flagged():
    # PE 在 facts 中，但数值不符 => medium
    facts = {"pe_ttm": 21.3}
    report = "该股 PE 为 99.9，明显高估。"
    res = reconcile(report, facts)
    assert any(m.severity == "medium" for m in res.mismatches)


def test_clean_report_no_mismatch():
    facts = {"pe_ttm": 21.3}
    report = "当前 PE 为 21.3（见基本面表），估值中性。"
    res = reconcile(report, facts)
    assert res.mismatches == []


def test_unit_confusion_flagged():
    facts = {"revenue": 200.0}  # 单位：亿元
    report = "营收达 200 万元。"  # 单位混淆
    res = reconcile(report, facts)
    assert any(m.severity == "medium" for m in res.mismatches)


def test_no_separator_claim_matched():
    # 无分隔符声明应能被抽取到：21.3 == facts，无 pe_ttm 疑点
    facts = {"pe_ttm": 21.3}
    report = "当前市盈率21.3，估值中性。"
    res = reconcile(report, facts)
    assert not any(m.metric == "pe_ttm" for m in res.mismatches)
    # 反例：虚构指标应判 critical
    res2 = reconcile("市盈率99.9明显高估。", {"price": 1685.0})
    assert any(m.severity == "critical" for m in res2.mismatches)
