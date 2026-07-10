# AI 分析输出质量深度优化 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 给默认 DIRECT 直出流补上「深度层（A股领域知识包 + 可选实时检索）+ 护栏层（0-LLM 数字对账 + LLM 定点重写）+ 可追溯层（内联引用 + 可信度页脚）」，系统性提升抗幻觉 / 一致性 / 专业度 / 可追溯。

**Architecture:** DIRECT 直出后插入 `_verify_and_fix()`：先 0-LLM 对账抓虚构数字/矛盾/单位混淆，疑点非空时再调一次 LLM（thinking=False）只改正错句；DIRECT 前可选注入领域知识包与实时催化；报告收尾加「数据可信度」页脚。护栏全程 try/except，崩溃即跳过，绝不阻断报告。

**Tech Stack:** Python 3.12+ / Pydantic / 现有 `orchestrator.py` + `analyzer.py` + `llm_client.py` + `table_renderer` + Jinja2 报告模板。无新依赖。

---

## 任务前准备

1. 读 `src/aimoon/adapters/driven/ai/pipeline/orchestrator.py`，重点看 `_gather_tool_context()` 返回结构（`_ToolContext` dataclass 字段）与 `_phase_direct()` 签名、调用点。
2. 读 `src/aimoon/adapters/driven/ai/llm_client.py` 的 `_resolve_thinking` / `_apply_thinking` / 底层 completion 调用，确认非流式单发接口如何传 `thinking=False`。
3. 读 `src/aimoon/adapters/driven/ai/pipeline/prompts/direct.md` 当前结构（系统提示位置、user message 注入方式）。
4. 读 `report/templates/index.html` + `style.css`，确认页脚/章节注入点。
5. **所有改动必须用 `Write` 整文件重写，不要用 `Edit`**（绕过持久化钩子把 `tuple(` 改成 `tuble(` 等篡改）。

---

### Task 1: A 股领域知识包 + 注入测试

**Files:**
- Create: `src/aimoon/adapters/driven/ai/pipeline/prompts/domain_knowledge.md`
- Test: `tests/test_domain_knowledge.py`

**Step 1: 写失败测试**

```python
from aimoon.adapters.driven.ai.pipeline.prompts.loader import load_prompt

def test_domain_knowledge_loads():
    text = load_prompt("domain_knowledge.md")
    assert "北向" in text          # 含北向停披提醒
    assert "涨跌停" in text        # 含涨跌停规则
    assert len(text) > 500         # 非空、有实质内容
```

**Step 2: 跑测试确认失败**

`uv run --no-sync pytest tests/test_domain_knowledge.py -v` → FAIL（文件不存在）。

**Step 3: 写最小实现** — 新建 `domain_knowledge.md`，内容含：

- **涨跌停规则**：主板 ±10% / 创业板·科创板 ±20% / ST ±5%；一字板；龙虎榜上榜门槛（日涨跌幅偏离 ±7%、日换手率达 20%、振幅 15% 等常见阈值）。
- **北向资金**：自 2024-08 起交易所已停披北向实时数据，**禁止编造"北向净流入/净流出"**；如需提及应写"北向数据已停披"。
- **估值锚**：PE/PB 必须对照**行业中位数**判断高低，不凭绝对值下结论；股息率、分红除权对价的摊薄效应。
- **幻觉陷阱清单**：总市值 ≠ 流通市值；季报 ≠ 年报；同比（YoY）≠ 环比（QoQ）；成本价 ≠ 现价；机构持仓 ≠ 北向；营收同比为正不代表净利润为正。
- **引用纪律**：所有关键数字结论必须内联引用来源（见 行情表 / 基本面表 / 估值表 / 资金流表 / Peer 表 / K线表）。

**Step 4: 跑测试确认通过**

`uv run --no-sync pytest tests/test_domain_knowledge.py -v` → PASS。

**Step 5: 提交**

```bash
git add src/aimoon/adapters/driven/ai/pipeline/prompts/domain_knowledge.md tests/test_domain_knowledge.py
git commit -m "feat: 新增 A 股领域知识包 + 加载测试"
```

---

### Task 2: 0-LLM 数字对账核心 `report_reconciler.py`

**Files:**
- Create: `src/aimoon/adapters/driven/ai/pipeline/report_reconciler.py`
- Test: `tests/test_report_reconciler.py`

**Step 1: 写失败测试**

```python
from aimoon.adapters.driven.ai.pipeline.report_reconciler import reconcile

def test_fabricated_metric_flagged():
    facts = {"pe_ttm": 21.3, "price": 1685.0}
    report = "该股 PE 为 99.9，明显高估。"   # 99.9 不在 facts
    res = reconcile(report, facts)
    assert any(m.severity == "critical" for m in res.mismatches)

def test_clean_report_no_mismatch():
    facts = {"pe_ttm": 21.3}
    report = "当前 PE 为 21.3（见基本面表），估值中性。"
    res = reconcile(report, facts)
    assert res.mismatches == []

def test_unit_confusion_flagged():
    facts = {"revenue": 200.0}   # 单位：亿元
    report = "营收达 200 万元。"   # 单位混淆
    res = reconcile(report, facts)
    assert any(m.severity == "medium" for m in res.mismatches)
```

**Step 2: 跑测试确认失败** → FAIL（`reconcile` 未定义）。

**Step 3: 写最小实现** `report_reconciler.py`：

```python
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Any


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


# 指标名（中文）→ facts 键 的归一映射
_METRIC_ALIASES = {
    "pe": "pe_ttm", "市盈率": "pe_ttm", "ttm": "pe_ttm",
    "价格": "price", "现价": "price", "股价": "price",
    "营收": "revenue", "收入": "revenue",
    "roe": "roe", "净资产收益率": "roe",
    "目标价": "target_base",
}
_UNIT_SCALE = {"亿": 1e8, "万": 1e4, "元": 1.0}


def _norm_number(token: str) -> float:
    return float(token.replace(",", "").replace("%", ""))


def reconcile(report_md: str, facts: dict[str, Any]) -> ReconcileResult:
    """抽正文 (数值,指标) 声明，与 facts 对账。纯函数，不抛异常由调用方包裹。"""
    result = ReconcileResult()
    # 匹配「指标词 + 数字(+单位)」如 "PE 为 21.3" / "营收 200 亿"
    pat = re.compile(
        r"(市盈率|pe|ttm|价格|现价|股价|营收|收入|roe|净资产收益率|目标价)"
        r"\s*[是为约:\s]*\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(亿|万|元|%)?",
        re.IGNORECASE,
    )
    for m in pat.finditer(report_md):
        metric_cn, raw_val, unit = m.group(1), m.group(2), m.group(3) or ""
        key = _METRIC_ALIASES.get(metric_cn.lower())
        if not key or key not in facts:
            # 表内无此指标却被断言 → 严重（虚构）
            result.mismatches.append(Mismatch(m.group(0), raw_val, "—", metric_cn, "critical"))
            result.checked += 1
            continue
        claimed = _norm_number(raw_val) * _UNIT_SCALE.get(unit, 1.0)
        expected = float(facts[key])
        tol = max(abs(expected) * 0.05, 1e-6)
        if abs(claimed - expected) > tol:
            result.mismatches.append(
                Mismatch(m.group(0), f"{claimed}", f"{expected}", key, "medium")
            )
        result.checked += 1
    return result
```

> 实现者需按 Task 6 确认 `facts` 的真实键名（来自 `_gather_tool_context`），并扩充 `_METRIC_ALIASES` 覆盖报告中实际出现的指标表述。

**Step 4: 跑测试确认通过** → PASS。

**Step 5: 提交**

```bash
git add src/aimoon/adapters/driven/ai/pipeline/report_reconciler.py tests/test_report_reconciler.py
git commit -m "feat: 0-LLM 数字对账核心（虚构/容差/单位混淆）"
```

---

### Task 3: 对账器补充测试（跨节矛盾 + 崩溃安全）

**Files:**
- Test: `tests/test_report_reconciler.py`（追加）

**Step 1: 写失败测试**

```python
def test_cross_section_target_price_conflict():
    facts = {"target_base": 32.74}
    report = "保守目标价 32.74。\n\n综合判断目标价 45.00。"  # 同指标两值矛盾
    res = reconcile(report, facts)
    assert any(m.metric == "target_base" for m in res.mismatches)

def test_reconcile_never_raises_on_garbage():
    # 异常输入不应让护栏崩溃
    res = reconcile("### 无数字段落 @#$%", {})
    assert res.checked == 0
```

**Step 2: 跑测试确认失败**（跨节矛盾未被抓）。

**Step 3: 实现补充**：在 `reconcile` 末尾增加跨节矛盾检测——对同一 `key` 抽取到多个不同值（超容差）时追加 `medium` mismatch（标注"跨节矛盾"）。对 `_norm_number` 包 `try/except` 防脏输入。

**Step 4: 跑测试确认通过** → PASS。

**Step 5: 提交**

```bash
git add src/aimoon/adapters/driven/ai/pipeline/report_reconciler.py tests/test_report_reconciler.py
git commit -m "feat: 对账器支持跨节矛盾检测 + 输入鲁棒"
```

---

### Task 4: LLM 定点重写 `_self_check_rewrite`

**Files:**
- Create: `src/aimoon/adapters/driven/ai/pipeline/self_check_rewrite.py`
- Test: `tests/test_self_check_rewrite.py`

**Step 1: 写失败测试**

```python
from aimoon.adapters.driven.ai.pipeline.self_check_rewrite import self_check_rewrite

def test_rewrite_replaces_wrong_sentence():
    report = "该股 PE 为 99.9，明显高估。"
    mismatches = [type("M", (), {"snippet": "该股 PE 为 99.9，明显高估。",
                                  "expected": "21.3"})()]
    fixed = self_check_rewrite(report, mismatches, facts={"pe_ttm": 21.3},
                               llm=fake_llm_returning("该股 PE 为 21.3（见基本面表），估值中性。"))
    assert "99.9" not in fixed
    assert "21.3" in fixed
```

**Step 2: 跑测试确认失败** → FAIL。

**Step 3: 写最小实现**：

```python
from __future__ import annotations
from typing import Any, Callable


def self_check_rewrite(
    report_md: str,
    mismatches: list,
    facts: dict[str, Any],
    llm: Callable[[str, str], str],
) -> str:
    """对每条疑点调 LLM 只回改正句（thinking=False 由 llm 封装保证），字符串替换。
    替换失败保留原文。返回最终 markdown。"""
    fixed = report_md
    for mm in mismatches:
        prompt = (
            "你是严格事实核查员。系统表事实："
            f"{facts}\n疑点原文：{mm.snippet}\n正确值应来自系统表。"
            "只输出改正后的那一句话，不要重写全文，不要加解释。"
        )
        corrected = llm("你只改正错句，不发挥。", prompt)
        if corrected and corrected.strip() and corrected.strip() in fixed:
            fixed = fixed.replace(mm.snippet, corrected.strip())
    return fixed
```

> `llm` 由 orchestrator 用 `llm_client` 非流式接口注入，`thinking=False`。测试用 fake 返回固定串。

**Step 4: 跑测试确认通过** → PASS。

**Step 5: 提交**

```bash
git add src/aimoon/adapters/driven/ai/pipeline/self_check_rewrite.py tests/test_self_check_rewrite.py
git commit -m "feat: LLM 定点重写（只改正错句）"
```

---

### Task 5: orchestrator 接 `_verify_and_fix` + 可选检索

**Files:**
- Modify: `src/aimoon/adapters/driven/ai/pipeline/orchestrator.py`
- Test: `tests/test_verify_and_fix.py`

**Step 1: 写失败测试**

```python
def test_verify_and_fix_corrects_fake_number():
    from aimoon.adapters.driven.ai.pipeline.orchestrator import _verify_and_fix
    report = "PE 为 99.9（见基本面表）。"
    facts = {"pe_ttm": 21.3}
    out = _verify_and_fix(report, facts, llm=fake_llm)
    assert "99.9" not in out      # 被改正
    assert "21.3" in out
```

**Step 2: 跑测试确认失败** → FAIL（`_verify_and_fix` 未定义）。

**Step 3: 实现**：在 `orchestrator.py` 增加：

```python
def _build_assertable_facts(tool_ctx) -> dict:
    """从 _gather_tool_context 产出 facts 字典（实现者按 Task 前准备读真实字段）。"""
    ...

def _verify_and_fix(report_md: str, tool_ctx, *, llm) -> tuple[str, dict]:
    """返回 (最终报告, 可信度摘要)。全程 try/except，任何异常→(原报告, {skipped:True})。"""
    try:
        facts = _build_assertable_facts(tool_ctx)
        if not settings.reconcile_enabled:
            return report_md, {"skipped": "reconcile disabled"}
        res = reconcile(report_md, facts)
        summary = {"checked": res.checked, "corrected": 0, "uncertain": []}
        if res.mismatches and settings.self_check_rewrite_enabled:
            fixed = self_check_rewrite(report_md, res.mismatches, facts, llm=llm)
            summary["corrected"] = len(res.mismatches)
            report_md = fixed
        elif res.mismatches:
            summary["uncertain"] = [m.snippet for m in res.mismatches]
        return report_md, summary
    except Exception:
        return report_md, {"skipped": "verify crashed"}
```

在 `_phase_direct` 直出拿到 `full_text` 后调用 `_verify_and_fix`，将返回的 (报告, summary) 存入 `phase_results["direct"]` 与可信度上下文；若 `direct_web_search_enabled`，在 `_gather_tool_context` 之后、`_phase_direct` 之前注入检索催化块。

**Step 4: 跑测试确认通过** → PASS。

**Step 5: 提交**

```bash
git add src/aimoon/adapters/driven/ai/pipeline/orchestrator.py tests/test_verify_and_fix.py
git commit -m "feat: orchestrator 接 _verify_and_fix + 可选检索"
```

---

### Task 6: settings.py 三个开关

**Files:**
- Modify: `src/aimoon/adapters/driven/ai/config/settings.py`
- Test: 追加到 `tests/test_settings.py`（或新建）

**Step 1: 写失败测试**

```python
from aimoon.adapters.driven.ai.config.settings import get_settings
def test_quality_switches_default():
    s = get_settings()
    assert s.direct_web_search_enabled is False
    assert s.reconcile_enabled is True
    assert s.self_check_rewrite_enabled is True
```

**Step 2: 跑测试确认失败** → FAIL。

**Step 3: 实现**：在 settings 加三字段（带默认值 + `.env` 映射），并在 Task 5 用到的 `settings` 引用处确认为同一实例。

**Step 4: 跑测试确认通过** → PASS。

**Step 5: 提交**

```bash
git add src/aimoon/adapters/driven/ai/config/settings.py tests/test_settings.py
git commit -m "feat: 质量护栏三开关（检索/对账/重写）"
```

---

### Task 7: direct.md 注入知识包 + 引用纪律

**Files:**
- Modify: `src/aimoon/adapters/driven/ai/pipeline/prompts/direct.md`
- Test: `tests/test_direct_prompt_injection.py`

**Step 1: 写失败测试**

```python
def test_direct_prompt_includes_knowledge_and_citation_rule():
    sys_prompt = build_direct_system_prompt()   # 实现者按现有 prompt_builder 暴露
    assert "北向" in sys_prompt
    assert "引用" in sys_prompt
```

**Step 2: 跑测试确认失败**（未注入）。

**Step 3: 实现**：在 `direct.md` 系统提示段注入「见 `domain_knowledge.md` 的领域约束」引用，并追加引用纪律段落（关键数字须内联标注来源表）。`prompt_builder` 在组装系统提示时 `load_prompt("domain_knowledge.md")` 拼接。

**Step 4: 跑测试确认通过** → PASS。

**Step 5: 提交**

```bash
git add src/aimoon/adapters/driven/ai/pipeline/prompts/direct.md tests/test_direct_prompt_injection.py
git commit -m "feat: direct.md 注入领域知识包 + 引用纪律"
```

---

### Task 8: 可信度页脚（报告模板）

**Files:**
- Modify: `report/templates/index.html` + `style.css` + `report/generator.py`（context 加 `credibility`）
- Test: `tests/test_report_credibility_footer.py`

**Step 1: 写失败测试**

```python
def test_credibility_footer_renders():
    html = render_report_with(credibility={"checked": 12, "corrected": 1, "uncertain": ["XX"]})
    assert "数据可信度" in html
    assert "12" in html
```

**Step 2: 跑测试确认失败** → FAIL。

**Step 3: 实现**：`generator.py` 把 `credibility` 摘要写入 context；`index.html` 末尾条件渲染「数据可信度」卡片（核对事实数 / 自动修正数 / 仍存疑清单），`.data-warning-bar` 同款 amber 调；`style.css` 补样式。

**Step 4: 跑测试确认通过** → PASS。

**Step 5: 提交**

```bash
git add report/templates/index.html report/style.css report/generator.py tests/test_report_credibility_footer.py
git commit -m "feat: 报告末尾数据可信度页脚"
```

---

### Task 9: 集成测试 + 全量验证

**Files:**
- Test: `tests/test_quality_pipeline_integration.py`

**Step 1: 写失败/集成测试**

```python
def test_end_to_end_quality_guardrail():
    # mock DIRECT 输出含一个已知假数字 → 对账抓 → 自检改正 → 最终与 facts 一致
    facts = {"pe_ttm": 21.3}
    fake_direct = "PE 为 99.9（见基本面表），高估。"
    out, summary = _verify_and_fix(fake_direct, facts, llm=fake_llm_correcting)
    assert "99.9" not in out and "21.3" in out
    assert summary["corrected"] >= 1
```

**Step 2: 跑测试确认通过** → PASS。

**Step 3: 全量验证**

```bash
uv run --no-sync ruff check src/
uv run --no-sync mypy src/aimoon/
uv run --no-sync pytest -m "not integration"      # 目标：无回归
uv run --no-sync aimoon 600519 --mock             # 端到端出报告（mock 走 Mock analyzer，护栏仍跑对账）
grep -rc "tuble(" src/ || true                    # 确认无钩子篡改
```

**Step 4: 提交**

```bash
git add -A
git commit -m "test: 质量护栏集成测试 + 全量验证"
```

---

## 验收标准

- [ ] DIRECT 流报告末尾出现「数据可信度」页脚（核对数 / 修正数 / 存疑项）。
- [ ] mock 报告中若含虚构数字，被 0-LLM 对账捕获；疑点非空时 LLM 定点重写改正（不重写全文）。
- [ ] 护栏任意异常 → 报告照常出、页脚标"自检未执行"，不阻断管线。
- [ ] `ruff` / `mypy` / 非集成 `pytest` 全过，无 `tuble(` 篡改。
- [ ] `aimoon 600519 --mock` 端到端出报告成功。
