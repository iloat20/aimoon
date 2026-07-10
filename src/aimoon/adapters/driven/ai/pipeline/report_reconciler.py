"""0-LLM 数字对账核心。

纯函数 reconcile(report_md, facts) 从研报文本中抽出「指标 + 数字」声明，
与给定的事实表 facts 对账，识别：
- critical：报告中断言了 facts 里不存在的指标（虚构指标）。
- medium：数值不符或单位混淆（在容差之外）。

本模块不依赖任何 LLM，也不抛异常；脏输入在内部兜底。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Mismatch:
    snippet: str
    claimed: str
    expected: str
    metric: str
    severity: str  # "critical" | "medium"


@dataclass
class ReconcileResult:
    mismatches: list[Mismatch] = field(default_factory=list)
    checked: int = 0


# 中文指标词 / 英文缩写 -> facts 键。顺序即别名覆盖优先级（靠前的优先匹配）。
_METRIC_ALIASES: dict[str, str] = {
    # 市盈率
    "市盈率": "pe_ttm",
    "pe": "pe_ttm",
    "ttm": "pe_ttm",
    "pe_ttm": "pe_ttm",
    "pettm": "pe_ttm",
    # 价格 / 现价 / 股价
    "价格": "price",
    "现价": "price",
    "股价": "price",
    "price": "price",
    # 营收 / 收入
    "营收": "revenue",
    "收入": "revenue",
    "revenue": "revenue",
    # ROE
    "roe": "roe",
    "净资产收益率": "roe",
    # 市净率
    "pb": "pb",
    "市净率": "pb",
    # 目标价
    "目标价": "target_base",
    "target": "target_base",
    "target_base": "target_base",
}

# 单位换算系数
_UNIT_FACTORS: dict[str, float] = {
    "亿": 1e8,
    "万元": 1e4,
    "万": 1e4,
    "元": 1.0,
}


def _normalize_metric(word: str) -> str | None:
    """将抽取到的指标词归一化为 facts 键；无法识别返回 None。"""
    key = word.strip().lower()
    return _METRIC_ALIASES.get(key)


def _parse_number(num_str: str, unit: str) -> float | None:
    """解析抽出数字 × 单位系数；失败返回 None。"""
    try:
        value = float(num_str)
    except (TypeError, ValueError):
        return None
    factor = _UNIT_FACTORS.get(unit, 1.0)
    return value * factor


# 匹配：指标词 + 可选连接词（为/达/约/空格等，零或多个）+ 数字 + 可选单位
# 例："PE 为 21.3" / "市盈率21.3" / "PE21.3" / "营收200亿" / "营收达 200 万元"
# metric 用惰性匹配 + 显式连接词集合，避免把"达"等连接动词吞进指标词。
# conn 设为可选（*）以便匹配指标词与数字直接相邻、无分隔符的写法（如"市盈率21.3"）。
_CLAIM_RE = re.compile(
    r"(?P<metric>[A-Za-z\u4e00-\u9fff]+?)"
    r"(?P<conn>(?:为|达|约|是|有|共|至|到|：|:|，|。|、|\(|（|\s)*)"
    r"(?P<num>\d+(?:\.\d+)?)"
    r"\s*"
    r"(?P<unit>亿|万元|万|元)?"
)


def reconcile(report_md: str, facts: dict) -> ReconcileResult:
    """对研报文本与事实表做数字对账，返回纯数据结果。

    不抛异常；无法解析的条目在内部跳过。

    facts 的值统一以基准单位存储（价格=元、营收=元、市值=元；百分比字段为小数或百分数按原值），
    调用方须遵守，否则单位会静默误判。
    """
    result = ReconcileResult()

    if not isinstance(report_md, str) or not isinstance(facts, dict):
        return result

    for match in _CLAIM_RE.finditer(report_md):
        metric_word = match.group("metric")
        num_str = match.group("num")
        unit = match.group("unit") or ""

        key = _normalize_metric(metric_word)
        if key is None:
            continue  # 不是已知指标词，跳过

        claimed_value = _parse_number(num_str, unit)
        if claimed_value is None:
            continue  # 数字解析失败，跳过

        result.checked += 1

        if key not in facts:
            # facts 里不存在该指标却被断言 => 虚构指标
            result.mismatches.append(
                Mismatch(
                    snippet=match.group(0),
                    claimed=num_str,
                    expected="<absent>",
                    metric=key,
                    severity="critical",
                )
            )
            continue

        try:
            expected_value = float(facts[key])
        except (TypeError, ValueError):
            continue

        tolerance = max(abs(expected_value) * 0.05, 1e-6)
        if abs(claimed_value - expected_value) > tolerance:
            result.mismatches.append(
                Mismatch(
                    snippet=match.group(0),
                    claimed=f"{claimed_value:g}",
                    expected=f"{expected_value:g}",
                    metric=key,
                    severity="medium",
                )
            )

    return result
