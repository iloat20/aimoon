# AI pipeline v2 实施计划 — Tasks 11-16 详细步骤

> 本文档是 `2026-07-05-ai-pipeline-v2.md` 的附录,详细展开 Tasks 11–16 每一步的代码骨架与验证命令。

---

## Task 11:`analyze(use_pipeline_v2=...)` 路由分岔

**Files:**
- Modify: `src/aimoon/adapters/driven/ai/analyzer.py`

- [ ] **Step 1:红 — 失败测试**

新建/扩展 `tests/test_ai.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_analyze_routes_to_legacy_when_flag_false():
    from aimoon.adapters.driven.ai.analyzer import DeepSeekAIAnalyzer
    # mock legacy
    with patch.object(DeepSeekAIAnalyzer, "_legacy_analyze", new_callable=AsyncMock) as m:
        m.return_value = AnalysisReport(symbol="600519", name="茅台", summary="leg", report_text="leg", investment_advice="x")
        az = DeepSeekAIAnalyzer(mock=True)
        out = await az.analyze(_si(), use_pipeline_v2=False)
        m.assert_called_once()
        assert out.report_text == "leg"

@pytest.mark.asyncio
async def test_analyze_routes_to_pipeline_when_flag_true():
    from aimoon.adapters.driven.ai.analyzer import DeepSeekAIAnalyzer
    with patch.object(DeepSeekAIAnalyzer, "_legacy_analyze", new_callable=AsyncMock), \
         patch.object(DeepSeekAIAnalyzer, "_pipeline_analyze", new_callable=AsyncMock) as m2:
        m2.return_value = AnalysisReport(symbol="600519", name="茅台", summary="v2", report_text="v2", investment_advice="x")
        az = DeepSeekAIAnalyzer(mock=True)
        out = await az.analyze(_si(), use_pipeline_v2=True)
        m2.assert_called_once()
        assert out.report_text == "v2"

def _si():
    from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
    return StockAnalysis(symbol="600519")
```

- [ ] **Step 2:绿** — `analyze` 顶端改为:

```python
async def analyze(self, stock_info, reports=None, financial_md_path=None, *, use_pipeline_v2: bool = False):
    if use_pipeline_v2:
        return await self._pipeline_analyze(stock_info, reports, financial_md_path)
    return await self._legacy_analyze(stock_info, reports, financial_md_path)
```

把现主逻辑整体移入 `_legacy_analyze()` 方法。

- [ ] **Step 3:红 → 绿验证**

- [ ] **Step 4:提交** `feat: analyze() use_pipeline_v2 路由,旧链路 100% 保留`

---

## Task 12:`_pipeline_analyze()` 接 orchestrator 五阶段

- 实施骨架:

```python
async def _pipeline_analyze(self, stock_info, reports=None, financial_md_path=None):
    from .pipeline.orchestrator import PipelineOrchestrator
    ctx = await PipelineOrchestrator(self).run(stock_info)
    # → AnalysisReport 转换
    text = ctx.get("final_markdown", "")
    self._write_cache(stock_info.symbol, text)
    return self._build_report(stock_info, text)
```

- 复用 `ai/cache.py` 的 `set_analysis_cache`
- 复用 `_sanitize_support_resistance` 与 disclaimer

---

## Task 13:PLAN / COLLECT / ANALYSIS 三阶段调 LLM + 工具

**关键代码骨架(COLLECT 核心路径):**

```python
phase_result = await self._call_phase(Phase.COLLECT, messages, stock_info)
# _call_phase 内:
tools_to_invoke = {name: TOOL_REGISTRY[name] for name in spec.tools if name != "web_search"}
# technicals / financial_temporal / peer_compare 并行
inner = await asyncio.gather(*[t(stock_info) for t in tools_to_invoke.values()])
# 拼装 tool_result messages,调 LLM 1 轮 → 看需不需要补 web_search
```

`TOOL_REGISTRY: dict[str, Callable]` = {
  technicals: tools.technicals.run,
  financial_temporal: tools.financial_temporal.run,
  ...
}

- **retries**:try/except → 捕获 phase 内部异常 → 2 次内重试 → 超出返 `__partial__`
- **超时**:`asyncio.wait_for(coro, timeout=spec.timeout_sec)` → 超时本阶段 `__partial__`

---

## Task 14:SELF_CHECK 阶段 — 5 项 JSON 校验

强制 JSON schema:

```python
CHECK_SCHEMA = {
    "citations_ok": bool,      # 每个关键数字标注源(训练数据/公司年报/搜索结果)
    "tables_ok": bool,         # 三张核心表格格式合规
    "trigger_ok": bool,        # 看空非泛泛(含触发条件)
    "advice_ok": bool,         # 投资建议明确(买/持/卖 + 价格区间 + 催化剂)
    "norepeat_ok": bool,       # 全文无重复段落
    "fixes_needed": list[str], # 未通过项的具体修复 schema
}
```

- GATE:任一 false → 把 `fixes_needed` 注入 ANALYSIS 历史重跑该子段(最多 1 次循环)
- Output schema 校验用 pydantic `model_validate`,schema 不符 → 重试一次 LLM

---

## Task 15:COMPILE + 5 个 phase prompt 最终稿

- 5 个 prompt 每份含:
  - `{{ stock_info }}` 占位(渲染后注入个股背景)
  - 阶段强制覆盖清单(来自 spec 3 节表格)
  - 格式约束(每表格 ≤6 行、结论在表后、全文中文)

- 阶段 5(COMPILE)复用 `_stream_final_response`,不另起一套

- 输出:**长 Markdown 终稿 + 注入 disclaimer + 写 L1 磁盘缓存**

---

## Task 16:e2e 实测 + CLI 开关

- 验收命令:

```bash
aimoon 600519 --use-v2
aimoon 000001 --use-v2
aimoon 601318 --use-v2
aimoon 600519 --legacy   # 旧链路对比
```

- Gate:三张核心表格出现率 100%,看空含触发条件 100%,单阶段失败不中断(pipeline 整体成功率 100%),`--legacy` 无回归

- CLI 开关位置:`adapters/driving/cli/main.py:argparse` 加 `--use-v2 / --legacy` 互斥组,透传至 `analyze(use_pipeline_v2: bool)`。
