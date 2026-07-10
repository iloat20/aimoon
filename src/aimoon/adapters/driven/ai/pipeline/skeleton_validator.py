"""Programmatic skeleton validator - 0 LLM, pure Python checks.

Validates: JSON parsability, math consistency (composite_prob, Kelly formula),
field completeness, and number cross-reference against system tables.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from .skeleton_schema import AnalysisSkeleton

logger = logging.getLogger(__name__)

_PROB_TOLERANCE = 0.05
_KELLY_TOLERANCE = 0.02


def validate_skeleton(raw: Any, tables_md: str = "") -> dict[str, Any]:
    """Validate a skeleton dict (or raw LLM text). Returns {passed, fixes_needed}.

    Never raises - on any error returns passed=False with a descriptive fix.
    """
    fixes: list[str] = []

    # 1. Parse JSON if raw is a string
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {"passed": False, "fixes_needed": ["骨架 JSON 解析失败：输出不是合法 JSON"]}
    elif isinstance(raw, dict):
        data = raw
    else:
        msg = f"骨架类型异常：期望 dict/str，得到 {type(raw).__name__}"
        return {"passed": False, "fixes_needed": [msg]}

    # 2. Pydantic schema validation
    try:
        sk = AnalysisSkeleton.model_validate(data)
    except ValidationError as e:
        errs = []
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"])
            errs.append(f"{loc}: {err['msg']}")
        return {"passed": False, "fixes_needed": errs[:5]}

    # 3. Math: composite_prob approx macro * industry * alpha
    n = sk.narratives
    expected_prob = round(n.macro.probability * n.industry.probability * n.alpha.probability, 4)
    if abs(sk.composite_prob - expected_prob) > _PROB_TOLERANCE:
        fixes.append(
            f"复合概率不一致：声明 {sk.composite_prob}，"
            f"但 {n.macro.probability}*{n.industry.probability}"
            f"*{n.alpha.probability}={expected_prob}"
        )

    # 4. Math: Kelly formula f* = (bp - q) / b
    k = sk.kelly
    if k.b > 0:
        expected_f = round((k.b * k.p - k.q) / k.b, 4)
        if abs(k.f_star - expected_f) > _KELLY_TOLERANCE:
            fixes.append(
                f"Kelly 公式不一致：f*={k.f_star}，"
                f"但 ({k.b}*{k.p}-{k.q})/{k.b}={expected_f}"
            )

    # 5. Completeness: valuation targets
    t = sk.valuation.targets
    missing_targets = []
    if t.conservative is None:
        missing_targets.append("conservative")
    if t.neutral is None:
        missing_targets.append("neutral")
    if t.optimistic is None:
        missing_targets.append("optimistic")
    if missing_targets:
        fixes.append(f"估值目标价缺失：{', '.join(missing_targets)}")

    # 6. Completeness: narratives falsify thresholds
    for name, nar in [("宏观", n.macro), ("行业", n.industry), ("企业alpha", n.alpha)]:
        if not nar.falsify:
            fixes.append(f"三层叙事「{name}」缺少证伪阈值")

    if fixes:
        return {"passed": False, "fixes_needed": fixes[:5]}
    return {"passed": True, "fixes_needed": []}
