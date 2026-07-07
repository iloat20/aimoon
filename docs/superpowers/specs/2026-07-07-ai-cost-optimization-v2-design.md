# AI Pipeline Cost Optimization Design

**Date:** 2026-07-07
**Scope:** Sub-project 1 of 3 (cost + flow simplification)
**Estimated savings:** ~35% per analysis run

## Changes

### 1. COMPILE reasoning_effort downgrade

**File:** `src/aimoon/adapters/driven/ai/pipeline/orchestrator.py`
**Current:** `_phase_compile()` calls `_call_llm_with_tools(messages, reasoning_effort="max")`
**Fix:** Change to `reasoning_effort="medium"`

COMPILE is a formatting/expansion phase (draft → polished long-form report). It does not require deep analytical reasoning — the thinking is already done in ANALYSIS. Using `"medium"` effort reduces reasoning tokens by ~50-60% for this phase, which accounts for ~60% of total pipeline reasoning cost.

### 2. ANALYSIS prompt inline self-check

**File:** `src/aimoon/adapters/driven/ai/pipeline/prompts/analysis.md`

Append instructions to the end of the analysis.md system prompt requiring the model to output a self-check JSON block after the draft:

```
## 自检

在草稿正文之后，输出以下 JSON 自检块（用 ```json 代码围栏）：

```json
{
  "passed": true/false,
  "fixes_needed": ["需要修复的具体问题描述"]
}
```

自检标准：
- 所有关键数字是否标注了来源？
- 八个章节是否都有实质内容（非空或占位）？
- 数字之间是否逻辑一致（如营收增速与绝对值匹配）？

如有问题，passed 设为 false，在 fixes_needed 中列出。
```

### 3. Orchestrator flow simplification

**File:** `src/aimoon/adapters/driven/ai/pipeline/orchestrator.py`

Changes:
1. In `_phase_analysis()`: after the draft is produced, parse the self-check JSON from the end of the draft text using the existing `_parse_self_check_json()` function. Store `fixes_needed` in the return dict's `checks` field.
2. In `_run_pipeline()`: remove the standalone `_phase_self_check()` call. The self-check result flows from `analysis_result["checks"]` into `prior["self_check_fixes"]` directly.
3. Delete the `_phase_self_check()` method entirely.
4. Keep `Phase.SELF_CHECK` in `phases.py` enum for backward compatibility but remove its PhaseSpec from `get_pipeline_phases()`.

### 4. Mode matrix

| Mode | Before | After |
|------|--------|-------|
| Default | ANALYSIS + SELF_CHECK + COMPILE (3 calls) | ANALYSIS(inline check) + COMPILE (2 calls) |
| fast | ANALYSIS + COMPILE (2 calls) | ANALYSIS(inline check) + COMPILE (2 calls, same) |
| single_call | ANALYSIS only (1 call) | No change |
| ultra_fast | ANALYSIS only (1 call) | No change |

## Testing

- Existing `_parse_self_check_json()` tests cover JSON parsing
- `test_orchestrator_runs_two_phases` needs update to verify 2-call flow (was 3-call)
- Mock pipeline should produce identical report structure
