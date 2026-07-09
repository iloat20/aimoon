# AI Pipeline 骨架扩写重构 — 实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 AI 分析 pipeline 从「双阶段重复生成」重构为「骨架+扩写」架构，token -45%、耗时 -40%、降级 0 LLM。

**Architecture:** ANALYSIS 输出 JSON 骨架（推理结论，不写文章）→ SELF_CHECK 程序化校验（0 LLM）→ COMPILE 基于骨架纯扩写。任何阶段失败都 0 LLM 降级到模板渲染。

**Tech Stack:** Python 3.12+ / Pydantic / pytest / DeepSeek API / asyncio

**Design doc:** `docs/plans/2026-07-10-ai-pipeline-refactor-design.md`

---

## 工作区注意

- 本机有持久化钩子篡改文件（`tuple(`→`tuble(`、插 `@pytest.mark.asyncio`）。**用 Write 整文件重写，不用 Edit**。
- 跑测试：`uv run --no-sync pytest -m "not integration"`
- 静态检查：`uv run --no-sync ruff check src/` + `uv run --no-sync mypy src/aimoon/`
- 端到端：`uv run --no-sync aimoon 600519 --mock -o output`

---

## Task 1: 骨架 Pydantic Model

**Files:**
- Create: `src/aimoon/adapters/driven/ai/pipeline/skeleton_schema.py`
- Test: `tests/test_skeleton_schema.py`

**Step 1: Write the failing test**

```python
# tests/test_skeleton_schema.py
"""Tests for the analysis skeleton JSON schema."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from aimoon.adapters.driven.ai.pipeline.skeleton_schema import AnalysisSkeleton


def _valid_skeleton() -> dict:
    """Return a minimal valid skeleton dict for tests."""
    return {
        "narratives": {
            "macro": {"probability": 0.6, "consensus": "x", "our_view": "y", "falsify": "z"},
            "industry": {"probability": 0.7, "consensus": "x", "our_view": "y", "falsify": "z"},
            "alpha": {"probability": 0.65, "consensus": "x", "our_view": "y", "falsify": "z"},
        },
        "composite_prob": 0.27,
        "forensic_audit": {
            "items": [{"item": "OCF", "status": "正常", "detail": "ok"}],
            "dupont": {"net_margin": 0.52, "turnover": 0.45, "leverage": 1.8},
            "quality_score": 8,
            "red_flags": ["应收增速超营收"],
        },
        "valuation": {
            "targets": {"conservative": 1500, "neutral": 1800, "optimistic": 2100},
            "implied_g": 0.04,
            "expectation_gap": "过度乐观",
        },
        "kelly": {"b": 2.5, "p": 0.27, "q": 0.73, "f_star": 0.04, "position": 0.02, "rating": "增持"},
    }


def test_valid_skeleton_parses():
    sk = AnalysisSkeleton.model_validate(_valid_skeleton())
    assert sk.kelly.b == 2.5
    assert sk.forensic_audit.quality_score == 8


def test_missing_kelly_fails():
    data = _valid_skeleton()
    del data["kelly"]
    with pytest.raises(ValidationError):
        AnalysisSkeleton.model_validate(data)


def test_probability_range_clamped():
    data = _valid_skeleton()
    data["narratives"]["macro"]["probability"] = 1.5
    with pytest.raises(ValidationError):
        AnalysisSkeleton.model_validate(data)


def test_quality_score_range():
    data = _valid_skeleton()
    data["forensic_audit.quality_score"] = 15
    with pytest.raises(ValidationError):
        AnalysisSkeleton.model_validate(data)
```

**Step 2: Run test to verify it fails**

```bash
uv run --no-sync pytest tests/test_skeleton_schema.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'aimoon.adapters.driven.ai.pipeline.skeleton_schema'`

**Step 3: Write minimal implementation**

```python
# src/aimoon/adapters/driven/ai/pipeline/skeleton_schema.py
"""Pydantic models for the ANALYSIS-phase JSON skeleton.

The skeleton is the structured output of ANALYSIS: all reasoning conclusions
(narratives, forensic audit, valuation, Kelly, red team, decision tree,
self-critique, stress test) as typed fields — no prose.

COMPILE consumes this skeleton to expand into a full report; SELF_CHECK
validates it programmatically (0 LLM).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class MissingDataItem(BaseModel):
    field: str
    importance: Literal["high", "medium", "low"] = "medium"
    estimable: bool = False


class DataInference(BaseModel):
    field: str
    formula: str = ""
    base: float | None = None
    optimistic: float | None = None
    pessimistic: float | None = None
    price_impact: str = ""


class Narrative(BaseModel):
    probability: float = Field(ge=0.0, le=1.0)
    consensus: str = ""
    our_view: str = ""
    falsify: str = ""


class Narratives(BaseModel):
    macro: Narrative
    industry: Narrative
    alpha: Narrative


class ForensicItem(BaseModel):
    item: str
    status: Literal["正常", "关注", "危险"] = "正常"
    detail: str = ""


class Dupont(BaseModel):
    net_margin: float | None = None
    turnover: float | None = None
    leverage: float | None = None


class ForensicAudit(BaseModel):
    items: list[ForensicItem] = Field(default_factory=list)
    dupont: Dupont = Field(default_factory=Dupont)
    quality_score: int = Field(ge=1, le=10)
    red_flags: list[str] = Field(default_factory=list)


class ValuationTargets(BaseModel):
    conservative: float | None = None
    neutral: float | None = None
    optimistic: float | None = None


class SensitivityItem(BaseModel):
    param: str = ""
    impact: str = ""


class Valuation(BaseModel):
    targets: ValuationTargets = Field(default_factory=ValuationTargets)
    implied_g: float | None = None
    peer_pe: dict[str, float | int] | None = None
    expectation_gap: str = ""
    sensitivity: list[SensitivityItem] = Field(default_factory=list)


class Kelly(BaseModel):
    b: float
    p: float = Field(ge=0.0, le=1.0)
    q: float = Field(ge=0.0, le=1.0)
    f_star: float
    position: float = 0.0
    rating: str = ""


class RedTeamItem(BaseModel):
    bull: str = ""
    bear: str = ""


class DecisionBranch(BaseModel):
    event: str = ""
    trigger: str = ""
    prob: float | None = None
    data_node: str = ""
    action_triggered: str = ""
    action_else: str = ""


class BearAttack(BaseModel):
    assumption: str = ""
    attack: str = ""


class SelfCritique(BaseModel):
    bear_attacks: list[BearAttack] = Field(default_factory=list)
    judge: str = ""


class StressTest(BaseModel):
    scenario: str = ""
    stress_fcf: float | None = None
    dividend_coverage: float | None = None
    floor_price: float | None = None
    floor_downside_pct: float | None = None
    verdict: str = ""


class AnalysisSkeleton(BaseModel):
    """Top-level skeleton: all reasoning conclusions as typed fields."""
    data_audit: dict | None = None
    data_inference: list[DataInference] = Field(default_factory=list)
    narratives: Narratives
    composite_prob: float = Field(ge=0.0, le=1.0)
    forensic_audit: ForensicAudit
    valuation: Valuation
    kelly: Kelly
    red_team: list[RedTeamItem] = Field(default_factory=list)
    decision_tree: list[DecisionBranch] = Field(default_factory=list)
    self_critique: SelfCritique = Field(default_factory=SelfCritique)
    stress_test: StressTest = Field(default_factory=StressTest)
```

**Step 4: Run test to verify it passes**

```bash
uv run --no-sync pytest tests/test_skeleton_schema.py -v
```
Expected: 4 passed

**Step 5: Lint + commit**

```bash
uv run --no-sync ruff check src/aimoon/adapters/driven/ai/pipeline/skeleton_schema.py
uv run --no-sync mypy src/aimoon/adapters/driven/ai/pipeline/skeleton_schema.py
git add src/aimoon/adapters/driven/ai/pipeline/skeleton_schema.py tests/test_skeleton_schema.py
git commit -m "feat: add AnalysisSkeleton Pydantic model for pipeline v2 refactor"
```

---

## Task 2: 骨架程序化校验器

**Files:**
- Create: `src/aimoon/adapters/driven/ai/pipeline/skeleton_validator.py`
- Test: `tests/test_skeleton_validator.py`

**Step 1: Write the failing test**

```python
# tests/test_skeleton_validator.py
"""Tests for the programmatic skeleton validator (0 LLM)."""
from __future__ import annotations

from aimoon.adapters.driven.ai.pipeline.skeleton_validator import validate_skeleton


def _valid():
    return {
        "narratives": {
            "macro": {"probability": 0.6, "consensus": "x", "our_view": "y", "falsify": "z"},
            "industry": {"probability": 0.7, "consensus": "x", "our_view": "y", "falsify": "z"},
            "alpha": {"probability": 0.65, "consensus": "x", "our_view": "y", "falsify": "z"},
        },
        "composite_prob": 0.27,
        "forensic_audit": {
            "items": [{"item": "OCF", "status": "正常", "detail": "ok"}],
            "dupont": {"net_margin": 0.52, "turnover": 0.45, "leverage": 1.8},
            "quality_score": 8,
            "red_flags": [],
        },
        "valuation": {
            "targets": {"conservative": 1500, "neutral": 1800, "optimistic": 2100},
            "implied_g": 0.04,
            "expectation_gap": "过度乐观",
        },
        "kelly": {"b": 2.5, "p": 0.27, "q": 0.73, "f_star": 0.04, "position": 0.02, "rating": "增持"},
    }


def test_valid_skeleton_passes():
    result = validate_skeleton(_valid(), tables_md="")
    assert result["passed"] is True
    assert result["fixes_needed"] == []


def test_invalid_json_returns_fix():
    result = validate_skeleton("not json at all", tables_md="")
    assert result["passed"] is False
    assert any("JSON" in f for f in result["fixes_needed"])


def test_composite_prob_mismatch():
    data = _valid()
    data["composite_prob"] = 0.99  # 0.6*0.7*0.65=0.273, not 0.99
    result = validate_skeleton(data, tables_md="")
    assert result["passed"] is False
    assert any("复合概率" in f for f in result["fixes_needed"])


def test_kelly_formula_check():
    data = _valid()
    data["kelly"]["f_star"] = 0.99  # (2.5*0.27-0.73)/2.5 = -0.012, not 0.99
    result = validate_skeleton(data, tables_md="")
    assert result["passed"] is False
    assert any("Kelly" in f for f in result["fixes_needed"])


def test_missing_valuation_targets():
    data = _valid()
    data["valuation"]["targets"] = {"conservative": 1500}  # missing neutral + optimistic
    result = validate_skeleton(data, tables_md="")
    assert result["passed"] is False
    assert any("目标价" in f for f in result["fixes_needed"])
```

**Step 2: Run test to verify it fails**

```bash
uv run --no-sync pytest tests/test_skeleton_validator.py -v
```
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# src/aimoon/adapters/driven/ai/pipeline/skeleton_validator.py
"""Programmatic skeleton validator — 0 LLM, pure Python checks.

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

    Never raises — on any error returns passed=False with a descriptive fix.
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
        return {"passed": False, "fixes_needed": [f"骨架类型异常：期望 dict/str，得到 {type(raw).__name__}"]}

    # 2. Pydantic schema validation
    try:
        sk = AnalysisSkeleton.model_validate(data)
    except ValidationError as e:
        errs = []
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"])
            errs.append(f"{loc}: {err['msg']}")
        return {"passed": False, "fixes_needed": errs[:5]}

    # 3. Math: composite_prob ≈ macro × industry × alpha
    n = sk.narratives
    expected_prob = round(n.macro.probability * n.industry.probability * n.alpha.probability, 4)
    if abs(sk.composite_prob - expected_prob) > _PROB_TOLERANCE:
        fixes.append(
            f"复合概率不一致：声明 {sk.composite_prob}，"
            f"但 {n.macro.probability}×{n.industry.probability}×{n.alpha.probability}={expected_prob}"
        )

    # 4. Math: Kelly formula f* = (bp - q) / b
    k = sk.kelly
    if k.b > 0:
        expected_f = round((k.b * k.p - k.q) / k.b, 4)
        if abs(k.f_star - expected_f) > _KELLY_TOLERANCE:
            fixes.append(
                f"Kelly 公式不一致：f*={k.f_star}，"
                f"但 ({k.b}×{k.p}-{k.q})/{k.b}={expected_f}"
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
    for name, nar in [("宏观", n.macro), ("行业", n.industry), ("企业α", n.alpha)]:
        if not nar.falsify:
            fixes.append(f"三层叙事「{name}」缺少证伪阈值")

    if fixes:
        return {"passed": False, "fixes_needed": fixes[:5]}
    return {"passed": True, "fixes_needed": []}
```

**Step 4: Run test to verify it passes**

```bash
uv run --no-sync pytest tests/test_skeleton_validator.py -v
```
Expected: 5 passed

**Step 5: Lint + commit**

```bash
uv run --no-sync ruff check src/aimoon/adapters/driven/ai/pipeline/skeleton_validator.py
uv run --no-sync mypy src/aimoon/adapters/driven/ai/pipeline/skeleton_validator.py
git add src/aimoon/adapters/driven/ai/pipeline/skeleton_validator.py tests/test_skeleton_validator.py
git commit -m "feat: add programmatic skeleton validator (0 LLM self-check)"
```

---

## Task 3: 骨架→Markdown 渲染器

**Files:**
- Create: `src/aimoon/adapters/driven/ai/pipeline/skeleton_renderer.py`
- Test: `tests/test_skeleton_renderer.py`

**Step 1: Write the failing test**

```python
# tests/test_skeleton_renderer.py
"""Tests for skeleton-to-Markdown renderer (degraded/fast mode output)."""
from __future__ import annotations

from aimoon.adapters.driven.ai.pipeline.skeleton_renderer import render_skeleton_md


def _valid():
    return {
        "narratives": {
            "macro": {"probability": 0.6, "consensus": "地产下行", "our_view": "企稳", "falsify": "利率>3.5%→-8%"},
            "industry": {"probability": 0.7, "consensus": "价格战", "our_view": "趋缓", "falsify": "持续>6月→-12%"},
            "alpha": {"probability": 0.65, "consensus": "稳定", "our_view": "改善", "falsify": "管理层变动→-15%"},
        },
        "composite_prob": 0.27,
        "forensic_audit": {
            "items": [{"item": "OCF/利润", "status": "正常", "detail": "1.2倍"}],
            "dupont": {"net_margin": 0.52, "turnover": 0.45, "leverage": 1.8},
            "quality_score": 8,
            "red_flags": ["应收增速超营收"],
        },
        "valuation": {
            "targets": {"conservative": 1500, "neutral": 1800, "optimistic": 2100},
            "implied_g": 0.04,
            "expectation_gap": "过度乐观",
        },
        "kelly": {"b": 2.5, "p": 0.27, "q": 0.73, "f_star": 0.04, "position": 0.02, "rating": "增持"},
        "red_team": [{"bull": "品牌强", "bear": "消费降级"}],
    }


def test_renders_all_sections():
    md = render_skeleton_md(_valid())
    assert "三层叙事" in md
    assert "法务会计" in md
    assert "估值" in md
    assert "Kelly" in md
    assert "增持" in md
    assert "1500" in md
    assert "1800" in md


def test_renders_empty_skeleton():
    md = render_skeleton_md(None)
    assert "数据缺失" in md or "暂不可用" in md


def test_includes_probabilities():
    md = render_skeleton_md(_valid())
    assert "60%" in md or "0.6" in md
    assert "27%" in md or "0.27" in md
```

**Step 2: Run test to verify it fails**

```bash
uv run --no-sync pytest tests/test_skeleton_renderer.py -v
```
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# src/aimoon/adapters/driven/ai/pipeline/skeleton_renderer.py
"""Render an analysis skeleton into readable Markdown.

Used in two paths:
1. Degraded mode — COMPILE fails, render skeleton directly (0 LLM).
2. Fast mode — use_fast/use_single_call skips COMPILE, render skeleton.
"""
from __future__ import annotations

import json
from typing import Any

from .skeleton_schema import AnalysisSkeleton


def render_skeleton_md(raw: Any) -> str:
    """Render a skeleton dict (or None) into a readable Markdown report."""
    if raw is None:
        return "# 分析报告（降级）\n\n数据缺失，无法生成完整分析。"

    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return "# 分析报告（降级）\n\n骨架 JSON 解析失败，数据暂不可用。"
    elif isinstance(raw, dict):
        data = raw
    else:
        return "# 分析报告（降级）\n\n骨架数据异常。"

    try:
        sk = AnalysisSkeleton.model_validate(data)
    except Exception:
        # Best-effort render from raw dict if schema validation fails
        return _render_raw(data)

    lines: list[str] = [f"# 分析报告（骨架渲染）\n"]

    # Narratives
    n = sk.narratives
    lines.append("## 三层叙事框架\n")
    for label, nar in [("宏观", n.macro), ("行业", n.industry), ("企业α", n.alpha)]:
        lines.append(f"### {label}（P={nar.probability:.0%}）")
        lines.append(f"- 共识：{nar.consensus}")
        lines.append(f"- 我们的解读：{nar.our_view}")
        lines.append(f"- 证伪阈值：{nar.falsify}\n")
    lines.append(f"**复合看多概率：{sk.composite_prob:.0%}**\n")

    # Forensic audit
    fa = sk.forensic_audit
    lines.append("## 法务会计审计\n")
    for item in fa.items:
        lines.append(f"- {item.item}：{item.status} — {item.detail}")
    d = fa.dupont
    if d.net_margin is not None:
        lines.append(f"\n**杜邦拆解**：净利率 {d.net_margin:.2f} × 周转率 {d.turnover:.2f} × 杠杆 {d.leverage:.2f}")
    lines.append(f"\n**盈利质量评分：{fa.quality_score}/10**")
    if fa.red_flags:
        lines.append(f"**红旗**：{', '.join(fa.red_flags)}\n")

    # Valuation
    v = sk.valuation
    t = v.targets
    lines.append("## 估值与目标价\n")
    lines.append(f"- 保守：{t.conservative} | 中性：{t.neutral} | 乐观：{t.optimistic}")
    if v.implied_g is not None:
        lines.append(f"- 隐含增长率 g*：{v.implied_g:.2%}")
    lines.append(f"- 预期差判断：{v.expectation_gap}\n")

    # Kelly
    k = sk.kelly
    lines.append("## 仓位量化（Kelly）\n")
    lines.append(f"- 评级：{k.rating}")
    lines.append(f"- b={k.b} | p={k.p:.0%} | q={k.q:.0%} | f*={k.f_star:.2%} | 建议仓位={k.position:.2%}\n")

    # Red team
    if sk.red_team:
        lines.append("## 反向论证\n")
        for rt in sk.red_team:
            lines.append(f"- 看多：{rt.bull} → 反证：{rt.bear}")
        lines.append("")

    # Decision tree
    if sk.decision_tree:
        lines.append("## 决策树\n")
        for br in sk.decision_tree:
            lines.append(f"- {br.event}：触发={br.trigger}（P={br.prob}）→ {br.action_triggered} / 否则 {br.action_else}")
        lines.append("")

    lines.append("> ⚠️ 本报告由 AI 自动生成（骨架渲染模式），数据与观点仅基于公开信息，不构成投资建议。")
    return "\n".join(lines)


def _render_raw(data: dict) -> str:
    """Best-effort render when schema validation fails — dump key fields."""
    lines = ["# 分析报告（降级 — 骨架校验未通过）\n"]
    lines.append("```json")
    lines.append(json.dumps(data, ensure_ascii=False, indent=2)[:3000])
    lines.append("```\n")
    lines.append("> ⚠️ 本报告由 AI 自动生成（降级模式），不构成投资建议。")
    return "\n".join(lines)
```

**Step 4: Run test to verify it passes**

```bash
uv run --no-sync pytest tests/test_skeleton_renderer.py -v
```
Expected: 3 passed

**Step 5: Lint + commit**

```bash
uv run --no-sync ruff check src/aimoon/adapters/driven/ai/pipeline/skeleton_renderer.py
uv run --no-sync mypy src/aimoon/adapters/driven/ai/pipeline/skeleton_renderer.py
git add src/aimoon/adapters/driven/ai/pipeline/skeleton_renderer.py tests/test_skeleton_renderer.py
git commit -m "feat: add skeleton-to-Markdown renderer for degraded/fast mode"
```

---

## Task 4: 骨架 JSON 容错解析

**Files:**
- Modify: `src/aimoon/adapters/driven/ai/pipeline/utils.py`
- Test: `tests/test_parse_skeleton.py`

**Step 1: Write the failing test**

```python
# tests/test_parse_skeleton.py
"""Tests for parse_skeleton_json — extract JSON from LLM output."""
from __future__ import annotations

from aimoon.adapters.driven.ai.pipeline.utils import parse_skeleton_json


def test_pure_json():
    raw = '{"kelly": {"b": 2.5}}'
    result = parse_skeleton_json(raw)
    assert result is not None
    assert result["kelly"]["b"] == 2.5


def test_json_in_code_fence():
    raw = 'Here is the skeleton:\n```json\n{"kelly": {"b": 2.5}}\n```\nDone.'
    result = parse_skeleton_json(raw)
    assert result is not None
    assert result["kelly"]["b"] == 2.5


def test_json_with_noise():
    raw = 'Analysis complete.\n{"kelly": {"b": 2.5}, "composite_prob": 0.27}\nEnd.'
    result = parse_skeleton_json(raw)
    assert result is not None
    assert result["composite_prob"] == 0.27


def test_invalid_returns_none():
    result = parse_skeleton_json("no json here at all")
    assert result is None


def test_empty_returns_none():
    result = parse_skeleton_json("")
    assert result is None
```

**Step 2: Run test to verify it fails**

```bash
uv run --no-sync pytest tests/test_parse_skeleton.py -v
```
Expected: FAIL — `ImportError: cannot import name 'parse_skeleton_json'`

**Step 3: Add parse_skeleton_json to utils.py**

Append to existing `src/aimoon/adapters/driven/ai/pipeline/utils.py` (after `parse_self_check_json`):

```python
def parse_skeleton_json(text: str) -> dict | None:
    """Extract a JSON object from LLM output text.

    Tries (in order):
    1. ```json code fence
    2. First { ... } block (greedy outermost)
    3. json.loads on the whole text

    Returns parsed dict or None on failure.
    """
    if not text or not text.strip():
        return None
    # 1. Prefer ```json fence
    m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(1).strip())
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    # 2. Outermost braces
    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        try:
            parsed = json.loads(text[first : last + 1])
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    # 3. Whole text
    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return None
```

**Step 4: Run test to verify it passes**

```bash
uv run --no-sync pytest tests/test_parse_skeleton.py -v
```
Expected: 5 passed

**Step 5: Lint + commit**

```bash
uv run --no-sync ruff check src/aimoon/adapters/driven/ai/pipeline/utils.py
git add src/aimoon/adapters/driven/ai/pipeline/utils.py tests/test_parse_skeleton.py
git commit -m "feat: add parse_skeleton_json for LLM output extraction"
```

---

## Task 5: 重写 analysis.md（JSON 骨架输出）

**Files:**
- Modify (rewrite): `src/aimoon/adapters/driven/ai/pipeline/prompts/analysis.md`

**Step 1: Rewrite the prompt**

Replace entire content of `analysis.md` with:

```markdown
# ANALYSIS 阶段 — 对冲基金大师级逆向策略师（输出 JSON 骨架）

你是一名对冲基金首席逆向策略师(大师级),兼任法证会计。你的工作不是「分析公司」,而是**找出市场错价**。

基于下方【系统预渲染数据】+【工具摘要】,输出一个**结构化 JSON 骨架**,包含全部推理结论。**不写完整文章,只输出 JSON。**

---

## 角色铁律

1. **证伪优先**:先努力推翻看多假设。
2. **法务会计眼光**:带着「数字可能是假的」怀疑看报表。
3. **隐含预期反推**:从估值反推市场在 pricing 什么。
4. **多维度验证**:每个核心结论至少两个维度交叉验证。
5. **数字必须有来源**:每个数字能在系统表格找到,无来源写 null。

---

## 输出格式

**只输出一个 JSON 对象,放在 ```json 代码块内,不要任何额外文字。**

```json
{
  "data_audit": {
    "missing": [{"field": "字段名", "importance": "high", "estimable": true}]
  },
  "data_inference": [{
    "field": "字段名", "formula": "反推公式",
    "base": 0.45, "optimistic": 0.55, "pessimistic": 0.30,
    "price_impact": "对目标价的影响描述"
  }],
  "narratives": {
    "macro":    {"probability": 0.6, "consensus": "当前共识", "our_view": "我们的解读", "falsify": "指标>临界值→目标价±Y%"},
    "industry": {"probability": 0.7, "consensus": "...", "our_view": "...", "falsify": "..."},
    "alpha":    {"probability": 0.65, "consensus": "...", "our_view": "...", "falsify": "..."}
  },
  "composite_prob": 0.27,
  "forensic_audit": {
    "items": [
      {"item": "OCF/利润背离", "status": "正常", "detail": "具体数据"},
      {"item": "应收vs营收", "status": "关注", "detail": "..."}
    ],
    "dupont": {"net_margin": 0.52, "turnover": 0.45, "leverage": 1.8},
    "quality_score": 8,
    "red_flags": ["红旗1", "红旗2"]
  },
  "valuation": {
    "targets": {"conservative": 1500, "neutral": 1800, "optimistic": 2100},
    "implied_g": 0.04,
    "expectation_gap": "过度乐观/悲观/合理",
    "sensitivity": [{"param": "折现率+1%", "impact": "-8%"}]
  },
  "kelly": {
    "b": 2.5, "p": 0.27, "q": 0.73, "f_star": 0.04,
    "position": 0.02, "rating": "买入/增持/持有/减持/回避"
  },
  "red_team": [{"bull": "看多逻辑", "bear": "反证"}],
  "decision_tree": [{
    "event": "事件名", "trigger": "触发条件", "prob": 0.25,
    "data_node": "数据发布日期",
    "action_triggered": "触发后操作", "action_else": "未触发操作"
  }],
  "self_critique": {
    "bear_attacks": [{"assumption": "脆弱假设", "attack": "空头攻击"}],
    "judge": "裁判回应与更新结论"
  },
  "stress_test": {
    "scenario": "极端情景描述",
    "stress_fcf": 3000000000,
    "dividend_coverage": 1.8,
    "floor_price": 1200,
    "verdict": "能维持/能维持但无余裕/无法维持"
  }
}
```

## 字段约束

- `probability`: 0-1 之间的小数(0.6 不是 60%)
- `composite_prob`: 必须约等于 macro × industry × alpha 三个概率之积
- `status`: 只能是 "正常"/"关注"/"危险"
- `quality_score`: 1-10 整数
- `f_star`: Kelly 公式 f* = (b×p - q) / b
- `position`: 建议仓位 = f* × 0.5(半凯利)
- 数字无来源时写 null,不要编造

## 数据纪律

- 已渲染表格的数字已精确计算,直接引用
- 每个数字能在【系统预渲染数据】或【工具摘要】中找到来源
- 无来源一律写 null + 在 detail 中说明「数据缺失」
```

**Step 2: Verify prompt loads**

```bash
uv run --no-sync python -c "from aimoon.adapters.driven.ai.pipeline.phases import _load, Phase; print(len(_load(Phase.ANALYSIS)))"
```
Expected: a positive number (prompt content length)

**Step 3: Commit**

```bash
git add src/aimoon/adapters/driven/ai/pipeline/prompts/analysis.md
git commit -m "refactor: rewrite analysis.md to output JSON skeleton"
```

---

## Task 6: 重写 compile.md（基于骨架扩写）

**Files:**
- Modify (rewrite): `src/aimoon/adapters/driven/ai/pipeline/prompts/compile.md`

**Step 1: Rewrite the prompt**

Replace entire content of `compile.md` with:

```markdown
# COMPILE 阶段 — 基于骨架扩写终稿

你是一名对冲基金首席逆向策略师(大师级)。你收到的是一个**结构化推理骨架**(JSON),你的任务是将它扩写为读者友好的完整长文报告。

**骨架是权威推理结论。你只负责扩写,不负责推理。**

标的快照:
{{ stock_info }}

## 系统预渲染数据(权威数字来源)
{{ tables_md }}

## 推理骨架(权威结论,每个数字必须与此一致)
{{ skeleton }}

上游工具摘要(可选参考):
{{ summary }}

自检备注(如有):
{{ self_check_fixes }}

---

## 核心规则

1. **骨架是权威**:骨架中的每个结论、每个数字都是经过深度推理确定的。你的任务是把它变成流畅的长文。
2. **禁止重新推理**:不要重新计算 Kelly、不要重新估算目标价、不要推翻骨架的结论。
3. **禁止编造**:骨架中没有的数字一律写「数据缺失」,不要编造。
4. **保留结构**:按骨架的逻辑顺序组织文章,每个骨架字段都要在文中体现。

---

## 报告结构(7 节,按序严格执行)

### 1. 数据采集与叙事基线
扩写骨架的 `data_audit` + `data_inference` + `narratives`。三层叙事每层展开为一段,附证伪阈值。末尾标注复合看多概率。

### 2. 法务会计审计
扩写骨架的 `forensic_audit`。逐项展开每个 item 的 detail,展开杜邦拆解,末尾给盈利质量评分和红旗。

### 3. 隐含市场预期反推 + 估值建模
扩写骨架的 `valuation`。展开 Gordon 模型 g*、预期差、三档目标价、敏感度分析。

### 4. 条件概率决策树
扩写骨架的 `decision_tree`。每个分支展开为触发条件+概率+数据节点+行动规则。

### 5. 看多逻辑 vs 反向论证
扩写骨架的 `red_team`。每个看多逻辑配 bear counter。

### 6. 投资策略 — Kelly 仓位管理
扩写骨架的 `kelly`。展开 b/p/q/f*/仓位/评级,附建仓执行建议。

### 7. 自我批判 + 情景应急 + 压力测试
扩写骨架的 `self_critique` + `stress_test`。空头攻击→裁判回应→极端情景→底线价。

### 8. 附录
关键缺失数据清单 + 数据源索引。

---

## 最终约束

- 输出完整 Markdown,不要 fences 外解释
- 全文中文,写充实但不堆砌
- 每个数字能在骨架或系统表格找到来源,否则写「数据缺失」
- 末段强制 disclaimer:
  > ⚠️ 本报告由 AI 自动生成,数据与观点仅基于公开财务报告及市场信息,不构成投资建议。
- 风格:专业、冷静、独立。不煽情,不营销。
```

**Step 2: Verify prompt loads + has skeleton placeholder**

```bash
uv run --no-sync python -c "from aimoon.adapters.driven.ai.pipeline.phases import _load, Phase; t=_load(Phase.COMPILE); assert '{{ skeleton }}' in t, 'missing skeleton placeholder'; print('OK')"
```
Expected: `OK`

**Step 3: Commit**

```bash
git add src/aimoon/adapters/driven/ai/pipeline/prompts/compile.md
git commit -m "refactor: rewrite compile.md to expand from JSON skeleton"
```

---

## Task 7: 删除 self_check.md + 更新 phases.py

**Files:**
- Delete: `src/aimoon/adapters/driven/ai/pipeline/prompts/self_check.md`
- Modify: `src/aimoon/adapters/driven/ai/pipeline/phases.py`

**Step 1: Delete self_check.md**

```bash
rm src/aimoon/adapters/driven/ai/pipeline/prompts/self_check.md
```

**Step 2: Update phases.py**

In `phases.py`, update `phase_system_prompt` to handle `{{ skeleton }}` placeholder, and update `PhaseSpec` for SELF_CHECK to mark it as non-LLM:

Add to `phase_system_prompt` replacements:
```python
    if "{{ skeleton }}" in template:
        import json
        skeleton_json = json.dumps(prior.get("skeleton") or prior.get("analysis_skeleton") or {}, ensure_ascii=False, default=str)
        replacements.append(("{{ skeleton }}", skeleton_json))
```

Update `get_pipeline_phases()` SELF_CHECK PhaseSpec:
```python
        PhaseSpec(
            Phase.SELF_CHECK,
            "",  # No prompt — programmatic validation, 0 LLM
            timeout_sec=5,     # seconds, not minutes — pure Python
            required_outputs=["validation result: passed + fixes_needed"],
        ),
```

**Step 3: Run existing tests to verify nothing breaks**

```bash
uv run --no-sync pytest tests/ -m "not integration" -v -k "phase or pipeline or orchestrator"
```
Expected: existing tests pass (may need to update mocks if they reference self_check.md)

**Step 4: Commit**

```bash
git add src/aimoon/adapters/driven/ai/pipeline/phases.py
git rm src/aimoon/adapters/driven/ai/pipeline/prompts/self_check.md
git commit -m "refactor: remove LLM self_check, switch to programmatic validation"
```

---

## Task 8: 重写 orchestrator.py（核心）

**Files:**
- Modify (major rewrite): `src/aimoon/adapters/driven/ai/pipeline/orchestrator.py`

This is the largest task. The orchestrator changes in 4 ways:
1. `_phase_analysis` — LLM output parsed as JSON skeleton (not prose draft)
2. `_phase_self_check` — calls `skeleton_validator.validate_skeleton()` (0 LLM)
3. `_phase_compile` — passes skeleton JSON via `{{ skeleton }}` placeholder
4. Degraded paths — use `skeleton_renderer.render_skeleton_md()` instead of legacy fallback
5. Tool scheduling — `asyncio.create_task` for dependency-triggered parallelism

**Step 1: Rewrite orchestrator.py**

Key changes to `_phase_analysis`:
- After LLM returns, call `parse_skeleton_json(draft)` to extract JSON
- Store skeleton in `prior["skeleton"]` (not `prior["analysis_draft"]`)
- If parse fails, return partial with raw text for degraded rendering

Key changes to `_phase_self_check`:
- Replace LLM call with `validate_skeleton(prior["skeleton"], ctx.system_tables_md)`
- No timeout needed (pure Python, <1s)

Key changes to `_phase_compile`:
- Pass `prior["skeleton"]` to `phase_system_prompt` (which injects `{{ skeleton }}`)
- User message: `# 推理骨架\n\n{json.dumps(skeleton)}`

Key changes to `_run_pipeline` degradation:
- ANALYSIS fail → render tables_md + data summary (0 LLM)
- COMPILE fail → `render_skeleton_md(prior["skeleton"])` (0 LLM)
- Remove `_legacy_analyze` fallback call

Key changes to tool scheduling in `_phase_analysis`:
```python
# Batch 2: risk + valuation + fcf parallel; scenario starts when val done
risk_task = asyncio.create_task(_run_safe(TOOL_RUNNERS["risk_quant"], fin, quote))
val_task = asyncio.create_task(_run_safe(TOOL_RUNNERS["valuation"], fin, quote, peer))
fcf_task = asyncio.create_task(_run_safe(TOOL_RUNNERS["fcf_dividend"], fin, financial, quote))

val = await val_task
scenario_task = asyncio.create_task(_run_safe(TOOL_RUNNERS["scenario_prob"], val, quote, fin))

risk = await risk_task
fcf = await fcf_task
scenario = await scenario_task
```

**Step 2: Write integration test**

```python
# tests/test_pipeline_skeleton_integration.py
"""Integration test: mock LLM returns valid skeleton, pipeline produces report."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_valid_skeleton_pipeline(mock_stock_analysis):
    """ANALYSIS returns valid JSON skeleton → COMPILE expands → report has content."""
    skeleton = {
        "narratives": {
            "macro": {"probability": 0.6, "consensus": "x", "our_view": "y", "falsify": "z"},
            "industry": {"probability": 0.7, "consensus": "x", "our_view": "y", "falsify": "z"},
            "alpha": {"probability": 0.65, "consensus": "x", "our_view": "y", "falsify": "z"},
        },
        "composite_prob": 0.27,
        "forensic_audit": {"items": [], "dupont": {}, "quality_score": 7, "red_flags": []},
        "valuation": {"targets": {"conservative": 100, "neutral": 120, "optimistic": 150}},
        "kelly": {"b": 2.0, "p": 0.27, "q": 0.73, "f_star": -0.05, "position": 0, "rating": "持有"},
    }
    llm_response = f"```json\n{json.dumps(skeleton)}\n```"

    # Mock LLM to return skeleton for ANALYSIS, prose for COMPILE
    with patch("aimoon.adapters.driven.ai.pipeline.llm_client.PipelineLlmClient") as MockClient:
        instance = MockClient.return_value
        instance.call_llm_with_stream = AsyncMock(return_value={"content": llm_response})
        instance.stream_llm_content = AsyncMock(return_value="# 报告\n\n基于骨架扩写的终稿。")
        instance.aclose = AsyncMock()

        # ... run pipeline, assert report contains skeleton-derived content
```

**Step 3: Run tests**

```bash
uv run --no-sync pytest tests/test_pipeline_skeleton_integration.py -v
uv run --no-sync pytest tests/ -m "not integration" -v
```

**Step 4: Lint + commit**

```bash
uv run --no-sync ruff check src/aimoon/adapters/driven/ai/pipeline/orchestrator.py
uv run --no-sync mypy src/aimoon/adapters/driven/ai/pipeline/orchestrator.py
git add src/aimoon/adapters/driven/ai/pipeline/orchestrator.py tests/test_pipeline_skeleton_integration.py
git commit -m "refactor: rewrite orchestrator for skeleton+expand architecture"
```

---

## Task 9: 更新 settings.py + analyzer.py 降级路径

**Files:**
- Modify: `src/aimoon/adapters/driven/config/settings.py`
- Modify: `src/aimoon/adapters/driven/ai/analyzer.py`

**Step 1: Update settings.py**

Change `deepseek_analysis_max_tokens` default from 8192 to 4096:

```python
    # 成本杠杆: ANALYSIS 阶段输出 token 上限。骨架 JSON 比旧初稿短得多
    # (800-1200 token vs 2500-3500 token),4096 已留充足余量。
    deepseek_analysis_max_tokens: int = 4096
```

**Step 2: Update analyzer.py _pipeline_analyze degradation**

In `_pipeline_analyze`, replace the legacy fallback with skeleton rendering:

```python
        if not text:
            # v2 失败时用骨架渲染(0 LLM),不再降级到 legacy
            from .pipeline.skeleton_renderer import render_skeleton_md
            skeleton = ctx.get("skeleton") if isinstance(ctx, dict) else None
            if skeleton:
                text = render_skeleton_md(skeleton)
                logger.info("[pipeline_v2] 降级到骨架渲染(0 LLM)")
            else:
                text = "# 分析报告（降级）\n\n数据采集或分析暂不可用。"
```

Remove the `legacy = await self._legacy_analyze(...)` fallback call.

**Step 3: Run tests + lint**

```bash
uv run --no-sync pytest tests/ -m "not integration" -v
uv run --no-sync ruff check src/aimoon/adapters/driven/config/settings.py src/aimoon/adapters/driven/ai/analyzer.py
uv run --no-sync mypy src/aimoon/adapters/driven/config/settings.py src/aimoon/adapters/driven/ai/analyzer.py
```

**Step 4: Commit**

```bash
git add src/aimoon/adapters/driven/config/settings.py src/aimoon/adapters/driven/ai/analyzer.py
git commit -m "refactor: lower max_tokens to 4096, replace legacy fallback with skeleton render"
```

---

## Task 10: 端到端验证 + 回归

**Step 1: Run full test suite**

```bash
uv run --no-sync pytest -m "not integration" -v
```
Expected: all pass (existing 179+ tests + new tests)

**Step 2: Static checks**

```bash
uv run --no-sync ruff check src/
uv run --no-sync mypy src/aimoon/
```
Expected: 0 errors

**Step 3: Mock end-to-end**

```bash
uv run --no-sync aimoon 600519 --mock -o output
```
Expected: HTML report generated, no crash, contains analysis content

**Step 4: Verify no tuble( injection**

```bash
grep -rc "tuble(" src/aimoon/adapters/driven/ai/pipeline/
```
Expected: 0 matches

**Step 5: Final commit**

```bash
git add -A
git commit -m "test: full regression pass for skeleton+expand pipeline refactor"
```

---

## 实施顺序总结

1. **Task 1**: skeleton_schema.py (Pydantic model)
2. **Task 2**: skeleton_validator.py (0-LLM 校验)
3. **Task 3**: skeleton_renderer.py (降级/快速模式渲染)
4. **Task 4**: parse_skeleton_json (utils.py 新增)
5. **Task 5**: analysis.md (JSON 骨架输出)
6. **Task 6**: compile.md (基于骨架扩写)
7. **Task 7**: 删 self_check.md + 更新 phases.py
8. **Task 8**: orchestrator.py 核心重写
9. **Task 9**: settings.py + analyzer.py 降级路径
10. **Task 10**: 端到端验证

Task 1-4 是独立的新模块，可以并行开发。Task 5-7 是提示词/配置改动。Task 8 依赖 1-7 全部完成。Task 9-10 是收尾。
