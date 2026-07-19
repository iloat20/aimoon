"""0-LLM 数字对账核心。

纯函数 reconcile(report_md, facts) 从研报文本中抽出「指标 + 数字」声明，
与给定的事实表 facts 对账，识别：
- critical：报告中断言了 facts 里不存在的指标（虚构指标）。
- medium：数值不符、单位混淆（在容差之外），或同一指标在报告内跨节矛盾。

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

# 已知别名按长度降序（子串匹配时优先选最长别名）。
# 「目标*」类须比「市盈率/营收」更长以在子串匹配时优先命中,避免
# 「目标市盈率21.3」被误归一化为当前 pe_ttm(I1 假阳性修复)。
_ALIASES_SORTED: list[str] = sorted(
    [
        "市盈率", "pe_ttm", "pettm", "价格", "现价", "股价", "price",
        "营收", "收入", "revenue", "roe", "净资产收益率", "pb", "市净率",
        "目标价", "target_base", "target", "pe", "ttm",
        "目标市盈率", "目标pe", "目标营收", "目标收入", "目标价格",
    ],
    key=len,
    reverse=True,
)

# 当前架构下无法抽取、缺失时不判虚构的指标。
# 例：现价 price —— _ToolContext 不含 quote 实体，price 取不到，缺失属预期，
# 不应把报告里的「现价 XXX 元」误判为虚构指标。
# 目标 PE / 目标营收同理为估值推导量，facts 通常不含，缺失时跳过(不判虚构)。
_OPTIONAL_METRICS: frozenset[str] = frozenset(
    {"price", "target_pe", "target_revenue"}
)

# 中文指标词 / 英文缩写 -> facts 键。
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
    "目标价格": "target_base",
    "target": "target_base",
    "target_base": "target_base",
    # 目标市盈率 / 目标营收 —— 与「当前」指标分离,防止把估值目标误当现值对账(I1)。
    "目标市盈率": "target_pe",
    "目标pe": "target_pe",
    "目标营收": "target_revenue",
    "目标收入": "target_revenue",
}

# 单位换算系数
_UNIT_FACTORS: dict[str, float] = {
    "亿": 1e8,
    "万元": 1e4,
    "万": 1e4,
    "元": 1.0,
}

# 数值容差：相对 5% 或绝对 1e-6 取较大者。
_TOLERANCE_REL = 0.05
_TOLERANCE_ABS = 1e-6


def _tolerance(expected: float) -> float:
    return max(abs(expected) * _TOLERANCE_REL, _TOLERANCE_ABS)


def _normalize_metric(word: str) -> str | None:
    """将抽取到的指标词归一化为 facts 键；无法识别返回 None。

    先尝试精确匹配；失败再退化为「子串包含」匹配，以容纳被前后文中文
    字符吞入的指标词（例如「保守目标价」中含「目标价」）。子串匹配优先
    取最长别名，避免短别名误吞（如「价」不会单独命中）。
    """
    key = word.strip().lower()
    exact = _METRIC_ALIASES.get(key)
    if exact is not None:
        return exact
    best: str | None = None
    for alias in _ALIASES_SORTED:
        if alias in key:
            best = alias
            break  # _ALIASES_SORTED 已按长度降序，首个即最长
    if best is None:
        return None
    return _METRIC_ALIASES[best]


def _parse_number(num_str: str, unit: str) -> float | None:
    """解析抽出数字 × 单位系数；失败返回 None。"""
    try:
        value = float(num_str.replace(",", ""))
    except (TypeError, ValueError, AttributeError):
        return None
    factor = _UNIT_FACTORS.get(unit, 1.0)
    return value * factor


# 匹配：指标词 + 可选连接词（为/达/约/空格等，零或多个）+ 数字 + 可选单位
# 例："PE 为 21.3" / "市盈率21.3" / "PE21.3" / "营收200亿" / "营收达 200 万元"
# metric 用惰性匹配 + 显式连接词集合，避免把"达"等连接动词吞进指标词。
# conn 设为可选（*）以便匹配指标词与数字直接相邻、无分隔符的写法（如"市盈率21.3"）。
_NUM_RE = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_CLAIM_RE = re.compile(
    r"(?P<metric>[A-Za-z\u4e00-\u9fff]+?)"
    r"(?P<conn>(?:为|达|约|是|有|共|至|到|：|:|=|，|。|、|\(|（|\s)*)"
    r"(?P<num>" + _NUM_RE + r")"
    r"\s*"
    r"(?P<unit>亿|万元|万|元)?"
)


def _to_float(value: object) -> float | None:
    """把 facts 值安全转成 float；任何异常返回 None。"""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, AttributeError):
        return None


def reconcile(report_md: str, facts: dict) -> ReconcileResult:
    """对研报文本与事实表做数字对账，返回纯数据结果。

    不抛异常；无法解析的条目在内部跳过。

    facts 的值统一以基准单位存储（价格=元、营收=元、市值=元；百分比字段为小数或百分数按原值），
    调用方须遵守，否则单位会静默误判。
    """
    result = ReconcileResult()

    if not isinstance(report_md, str) or not isinstance(facts, dict):
        return result

    # key -> [(claimed_value, snippet, num_str), ...]：记录每节抽取到的声明，供跨节矛盾复用。
    occurrences: dict[str, list[tuple[float, str, str]]] = {}

    try:
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

            snippet = match.group(0)
            result.checked += 1
            occurrences.setdefault(key, []).append((claimed_value, snippet, num_str))

            if key not in facts:
                if key in _OPTIONAL_METRICS:
                    continue  # 取不到即跳过，不误判虚构（如现价 price）
                # facts 里不存在该指标却被断言 => 虚构指标
                result.mismatches.append(
                    Mismatch(
                        snippet=snippet,
                        claimed=num_str,
                        expected="<absent>",
                        metric=key,
                        severity="critical",
                    )
                )
                continue

            expected_value = _to_float(facts[key])
            if expected_value is None:
                continue  # facts 值无法转成数字，跳过

            if abs(claimed_value - expected_value) > _tolerance(expected_value):
                result.mismatches.append(
                    Mismatch(
                        snippet=snippet,
                        claimed=f"{claimed_value:g}",
                        expected=f"{expected_value:g}",
                        metric=key,
                        severity="medium",
                    )
                )
    except Exception:
        # 极端脏输入兜底：主体异常也不外抛，返回已收集结果。
        return result

    # 跨节矛盾检测：同一指标键在报告内被抽取到 ≥2 个相互超容差的不同数值。
    # 仅对 facts 中存在的指标触发（虚构指标已由 critical 处理，不再叠加）。
    try:
        for key, occs in occurrences.items():
            if key not in facts or len(occs) < 2:
                continue
            first_value = occs[0][0]
            for value, snippet, _num_str in occs[1:]:
                if abs(value - first_value) > _tolerance(first_value):
                    result.mismatches.append(
                        Mismatch(
                            snippet=f"跨节矛盾：{snippet}",
                            claimed=f"{value:g}",
                            expected=f"{first_value:g}",
                            metric=key,
                            severity="medium",
                        )
                    )
    except Exception:
        # 跨节检测异常同样不外抛。
        pass

    return result
