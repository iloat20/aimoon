"""report_reconciler 数字对账的边界测试。

这些用例覆盖「检查 bug」轮次发现的对账缺陷：
- price 别名被误判虚构（架构上 _ToolContext 无 quote，price 取不到）
- 等号写法 pb=1.2 / PE=21 漏检
- 千分位 1,680 漏检
"""

from aimoon.adapters.driven.ai.pipeline.report_reconciler import reconcile


def test_price_absent_not_flagged_critical():
    """现价无法抽取进 facts，报告含现价不应误判为虚构指标。"""
    res = reconcile("当前股价 1680 元，处于历史高位。", {"pe_ttm": 21.3})
    price_mm = [m for m in res.mismatches if m.metric == "price"]
    assert price_mm == []


def test_equals_sign_claim_is_matched():
    """等号写法 pb=9.9 应能被抽取；值不符时应检出 mismatch（否则漏检）。"""
    res = reconcile("市净率 pb=9.9，估值偏高。", {"pb": 1.2})
    pb_mm = [m for m in res.mismatches if m.metric == "pb"]
    assert pb_mm  # 修复前等号写法漏检 => 此断言失败


def test_thousands_separator_is_matched():
    """千分位 1,680 亿元应能被解析并与 facts 对齐。"""
    res = reconcile("年营收 1,680 亿元，稳健增长。", {"revenue": 1.68e11})
    rev_mm = [m for m in res.mismatches if m.metric == "revenue"]
    assert rev_mm == []
