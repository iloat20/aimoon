# AI 分析 pipeline v2 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (recommended) or superpowers:executing-plans。Steps use checkbox (`- [ ]`) for tracking.

**Goal:** 将 aimoon 的一段式 AI 分析升级为"五阶段模板化递进流水线 + 6 个扩展工具 + SELF 自检闭环",消除研究忽深忽浅、看空逻辑流于形式、幻觉/重复/格式问题。

**Architecture:** Pipeline 位于 `adapters/driven/ai/`,是新的**并行路由**,与旧 `_legacy_analyze()` 共存,由 `analyze(use_pipeline_v2=...)` 切换。工具实现为 `ai/tools/` 下的纯函数模块(只有 `peer_compare` 组合 `web_search_tool`),工具**不过端口**。`StockAnalysis` 聚合新增可选字段 `history_financial`(近 3 年报),由 `AkshareFinancialAdapter.fetch_history()` 小扩采集。超时总硬上限 300 秒,每阶段独立降级 `[partial]`。

**Tech Stack:** Python 3.12+, httpx, asyncio, akshare, pandas, numpy, DeepSeek v4-flask API

---

## 模块与文件总览

| 文件 | 类型 | 职责 |
|---|---|---|
| `src/.../aggregates/stock_analysis.py` | 修改 | 加 `history_financial` 字段 |
| `src/.../financial/akshare_adapter.py` | 修改 | 加 `fetch_history()` |
| `src/.../collectors/composite_repo.py` | 修改 | 注入 history 到聚合 |
| `src/.../ai/pipeline/phases.py` | **新建** | Phase 枚举 + PhaseSpec + system prompt 加载 |
| `src/.../ai/pipeline/orchestrator.py` | **新建** | 串联 phases + 重试 + 超时 + 降级 |
| `src/.../ai/pipeline/_phase_cache.py` | **新建** | L2 阶段级内存缓存 |
| `src/.../ai/pipeline/prompts/phase_1..5.md` | **新建** ×5 | 每阶段 system prompt |
| `src/.../ai/tools/technicals.py` | **新建** | 均线/MACD/RSI/布林带/量比/资金流 |
| `src/.../ai/tools/financial_temporal.py` | **新建** | 3 年财务时序 + CAGR |
| `src/.../ai/tools/peer_compare.py` | **新建** | 同行业竞品对比 |
| `src/.../ai/tools/business_moat.py` | **新建** | SWOT/护城河/OCF 含金量 |
| `src/.../ai/tools/risk_quant.py` | **新建** | 三看空含触发条件 + 看多 |
| `src/.../ai/tools/valuation.py` | **新建** | PE/PB + FCFE 三档目标价 |
| `src/.../ai/analyzer.py` | 修改 | `use_pipeline_v2` 路由 |
| `tests/test_pipeline_phases.py` | **新建** | 阶段状态 + 聚合扩展 |
| `tests/test_tools_*.py` | **新建** ×6 | 6 工具单测 |
| `tests/test_integration_pipeline.py` | **新建** | 600519/000001/601318 e2e |
| `docs/superpowers/plans/2026-07-05-ai-pipeline-v2-tasks-11-16.md` | **新建** | 后续 tasks 11-16 的详细步骤 |

---

## 五阶段流水线与强制门

| 阶段 | 工具(必须返回非空) | 强制覆盖清单 | 超时 |
|---|---|---|---|
| PLAN | 模型可选 web_search | 子任务 ≥8,覆盖四框架 | 30s |
| COLLECT | 并行:`technicals` `financial_temporal` `peer_compare` + 模型自选 web_search | 三工具全非空;竞品 ≥3;时序 ≥3 年 | 60s |
| ANALYSIS | 串行:`risk_quant` → `valuation`/`business_moat` 并行 + 模型自选 web_search | 三看空含触发条件+估值冲击%;三档估值含假设 | 120s |
| SELF_CHECK | 纯 LLM 自检 | JSON 5 项:数字源标注 / 表格合规 / 触发条件 / 投资建议明确 / 无重复 | 30s |
| COMPILE | 复用 `_stream_final_response` | 完整 Markdown + disclaimer + 写盘缓存 | 60s |

**总硬上限 300 秒**。超时后:已完成阶段保留 + 未完成阶段占位符 → 仍输出降稿 `[超时降级]`。

**失败降级契约:** 每个工具失败返回 `{"__partial__": true, "reason": "..."}` 不抛异常。每阶段最多 2 次,2 次失败本阶段标 `[partial]` 但后续阶段继续。

---

## 阶段门(Gate)总览

| 阶段 | 验收 |
|---|---|
| P1 done | `fetch_history("600519")` 返 ≥1 年,旧 `fetch()` 仍可用 |
| P2 done | 桩股票跑 5 阶段无崩溃;technicals/financial_temporal 单测 100% |
| P3 done | 600519/000001/601318 实跑出三张核心表格 + 看空非空 |
| P4 done | `aimoon <code>` 默认走 v2;`--legacy` 回退旧链路 |

---

## Task 1:StockAnalysis 加 `history_financial`

**Files:**
- Modify: `src/aimoon/core/domain/aggregates/stock_analysis.py`
- Create: `tests/test_pipeline_phases.py`

- [ ] **Step 1:红 — 失败测试**

```python
from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
from aimoon.core.domain.entities.financial import FinancialData

def test_history_financial_defaults_to_empty():
    assert StockAnalysis(symbol="600519").history_financial == []

def test_history_financial_accepts_list():
    h = [FinancialData(symbol="600519", report_period="2024-12-31"),
         FinancialData(symbol="600519", report_period="2023-12-31")]
    agg = StockAnalysis(symbol="600519", history_financial=h)
    assert len(agg.history_financial) == 2
```

- [ ] **Step 2:运行(Red)** `uv run pytest tests/test_pipeline_phases.py -v`

- [ ] **Step 3:绿** — 在 `quarterly_report` 字段后加:

```python
history_financial: list[FinancialData] = Field(default_factory=list)
```

- [ ] **Step 4:运行(Green)** 同上

- [ ] **Step 5:提交** `git commit -m "feat: StockAnalysis 新增 history_financial 字段,支撑 pipeline v2 的历史财务时序分析"`

---

## Task 2:`AkshareFinancialAdapter.fetch_history()` 拉 3 年年报

**Files:**
- Modify: `src/aimoon/adapters/driven/financial/akshare_adapter.py`
- Modify: `tests/test_pipeline_phases.py`
- Modify: `pyproject.toml`(加 `integration` marker)

- [ ] **Step 1:红 — 失败测试**

```python
import pytest
from aimoon.adapters.driven.financial.akshare_adapter import AkshareFinancialAdapter

@pytest.mark.integration
@pytest.mark.asyncio
async def test_fetch_history_returns_up_to_n_years():
    adapter = AkshareFinancialAdapter()
    result = await adapter.fetch_history("600519", years=3)
    assert isinstance(result, list) and 1 <= len(result) <= 3
    periods = [r.report_period for r in result if r.report_period]
    assert periods == sorted(periods, reverse=True)

@pytest.mark.asyncio
async def test_fetch_history_bad_symbol_returns_empty():
    assert await AkshareFinancialAdapter().fetch_history("999999", years=3) == []
```

- [ ] **Step 2:运行(Red)** `uv run pytest tests/test_pipeline_phases.py -k fetch_history -v`

- [ ] **Step 3:绿** — 在 `AkshareFinancialAdapter` 末尾追加:

```python
async def fetch_history(self, symbol: str, years: int = 3) -> list[FinancialData]:
    prefix = "SH" if symbol.startswith("6") else "SZ" if symbol.startswith("0") else "BJ"
    try:
        df = await asyncio.to_thread(ak.stock_profit_sheet_by_report_em, f"{prefix}{symbol}")
    except Exception as e:
        logger.debug("[akshare] fetch_history failed: %s", e)
        return []
    if df is None or df.empty:
        return []
    if "REPORT_TYPE" in df.columns:
        df = df[df["REPORT_TYPE"] == "年报"]
    if "REPORT_DATE" in df.columns:
        df = df.sort_values("REPORT_DATE", ascending=False)
    return self._parse_top_n(df, symbol, years)

def _parse_top_n(self, df, symbol, n):
    out = []
    for _, row in df.head(n).iterrows():
        fd = FinancialData(symbol=symbol, source="akshare(东方财富)")
        rd = row.get("REPORT_DATE")
        if rd is not None:
            fd.report_period = str(rd)[:10]
        def _set(field, col, *, transform=float):
            v = row.get(col)
            if pd.notna(v):
                setattr(fd, field, transform(v))
        _set("revenue", "TOTAL_OPERATE_INCOME", transform=lambda v: float(v) if float(v) > 0 else 0)
        _set("revenue_yoy", "TOTAL_OPERATE_INCOME_YOY", transform=lambda v: float(v) if float(v) != 0 else 0)
        _set("net_profit", "NETPROFIT", transform=lambda v: float(v) if float(v) != 0 else 0)
        _set("net_profit_yoy", "NETPROFIT_YOY")
        _set("eps", "BASIC_EPS", transform=lambda v: float(v) if float(v) > 0 else 0)
        _set("total_assets", "TOTAL_ASSETS")
        _set("total_liabilities", "TOTAL_LIABILITIES")
        _set("operating_cf", "NETCASH_OPERATE")
        if fd.total_assets > 0:
            fd.equity = fd.total_assets - fd.total_liabilities
        if fd.net_profit != 0 and fd.equity > 0:
            fd.roe = round(fd.net_profit / fd.equity * 100, 2)
        out.append(fd)
    return out
```

- [ ] **Step 4:运行(Green)** 同上,需联网。

- [ ] **Step 5:提交** `git commit -m "feat: AkshareFinancialAdapter.fetch_history() 拉近 3 年年报"`

---

## Task 3:`CompositeStockAnalysisRepository` 灌 history 到聚合

**Files:**
- Modify: `src/aimoon/adapters/driven/collectors/composite_repo.py`
- Modify: `tests/test_pipeline_phases.py`

- [ ] **Step 1:红 — 失败测试**

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_collect_all_populates_history_financial():
    from aimoon.adapters.driven.financial.akshare_adapter import AkshareFinancialAdapter
    repo = CompositeStockAnalysisRepository(financial_collector=AkshareFinancialAdapter())
    agg = await repo.collect_all("600519")
    assert isinstance(agg.history_financial, list) and len(agg.history_financial) >= 1
    assert agg.financial.report_period  # 旧字段仍在
```

- [ ] **Step 2:运行(Red)** `uv run pytest -k test_collect_all_populates_history -v`

- [ ] **Step 3:绿** — `_collect_all_inner` 中:

```python
results = await asyncio.gather(
    self._fetch_quote(symbol, name),              # 0
    self._collect_financial(symbol),               # 1
    self._collect_quarterly_financial(symbol),     # 2
    self._kline_collector.fetch(symbol),           # 3
    self._capital_flow_collector.fetch(symbol),    # 4
    self._research_collector.fetch(symbol),        # 5
    self._collect_history_financial(symbol),       # 6 新
    return_exceptions=True,
)
...
# 改 _unwrap,index 6 对应 list
history = self._unwrap(
    results[6], list,
    symbol=symbol, platform="历史财务",
    ok=lambda d: isinstance(d, list) and len(d) >= 1,
    msg=lambda d: f"   历史财务: {len(d)} 年年报",
    fail="   历史财务: 获取失败",
    elapsed_ms=elapsed_ms,
)
...
return StockAnalysis(..., history_financial=history if isinstance(history, list) else [])
```

新增方法:

```python
async def _collect_history_financial(self, symbol: str) -> list[FinancialData]:
    if self._financial_collector is not None and hasattr(self._financial_collector, "fetch_history"):
        return await self._financial_collector.fetch_history(symbol)
    return []
```

- [ ] **Step 4:Green**

- [ ] **Step 5:提交** `git commit -m "feat: composite_repo 注入 history_financial 到 StockAnalysis"`

---

## Task 4:Pipeline 骨架 — 五阶段状态机 + 占位 system prompt

**Files(全新建):**
- `src/aimoon/adapters/driven/ai/pipeline/__init__.py`
- `src/aimoon/adapters/driven/ai/pipeline/phases.py`
- `src/aimoon/adapters/driven/ai/pipeline/orchestrator.py`
- `src/aimoon/adapters/driven/ai/pipeline/_phase_cache.py`
- `src/aimoon/adapters/driven/ai/pipeline/prompts/{plan,collect,analysis,self_check,compile}.md`
- Modify: `tests/test_pipeline_phases.py`

- [ ] **Step 1:红 — 失败测试**

```python
from aimoon.adapters.driven.ai.pipeline.phases import Phase, get_pipeline_phases
from aimoon.adapters.driven.ai.pipeline.orchestrator import PipelineOrchestrator

def test_five_phases_defined():
    assert len(Phase) == 5
    assert Phase.PLAN.value == "plan"

def test_pipeline_specs_have_required_fields():
    for spec in get_pipeline_phases():
        assert spec.system_prompt_template
        assert spec.timeout_sec > 0

@pytest.mark.asyncio
async def test_orchestrator_runs_all_phases_placeholder():
    class FakeAnalyzer: pass
    ctx = await PipelineOrchestrator(FakeAnalyzer()).run(FakeAggregator())
    assert isinstance(ctx, dict)
```

- [ ] **Step 2:运行(Red)**

- [ ] **Step 3:绿**

`phases.py`:

```python
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

class Phase(str, Enum):
    PLAN = "plan"; COLLECT = "collect"; ANALYSIS = "analysis"
    SELF_CHECK = "self_check"; COMPILE = "compile"

PROMPTS_DIR = Path(__file__).parent / "prompts"

@dataclass
class PhaseSpec:
    phase: Phase
    system_prompt_template: str
    tools: list[str] = field(default_factory=list)
    timeout_sec: int = 60
    max_retries: int = 2
    required_outputs: list[str] = field(default_factory=list)

def _load(phase: Phase) -> str:
    p = PROMPTS_DIR / f"phase_{phase.value}.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""

def get_pipeline_phases() -> list[PhaseSpec]:
    return [
        PhaseSpec(Phase.PLAN, _load(Phase.PLAN),
                  timeout_sec=30, required_outputs=["子任务 ≥8"]),
        PhaseSpec(Phase.COLLECT, _load(Phase.COLLECT),
                  tools=["technicals", "financial_temporal", "peer_compare", "web_search"],
                  timeout_sec=60, required_outputs=["三工具全非空"]),
        PhaseSpec(Phase.ANALYSIS, _load(Phase.ANALYSIS),
                  tools=["risk_quant", "valuation", "business_moat", "web_search"],
                  timeout_sec=120, required_outputs=["三看空含触发条件", "三档估值"]),
        PhaseSpec(Phase.SELF_CHECK, _load(Phase.SELF_CHECK),
                  timeout_sec=30, required_outputs=["5 项 JSON 校验"]),
        PhaseSpec(Phase.COMPILE, _load(Phase.COMPILE),
                  timeout_sec=60, required_outputs=["长 Markdown"]),
    ]
```

`_phase_cache.py`:

```python
import hashlib, json, time
from typing import Any
_cache: dict[str, tuple[float, Any]] = {}

def _fingerprint(si):
    seed = json.dumps({"s": si.symbol,
                       "p": getattr(si.quote, "price", None),
                       "r": getattr(si.financial, "revenue", 0),
                       "cf": getattr(si.capital_flow, "main_net_5d", 0),
                       "kc": len(getattr(si.kline, "bars", []))},
                      sort_keys=True, default=str)
    return hashlib.sha1(seed.encode()).hexdigest()[:16]

def cache_key(si, phase): return f"{si.symbol}:{_fingerprint(si)}:{phase}"
def get_phase_cache(si, phase):
    return (v[1] if (v := _cache.get(cache_key(si, phase))) else None)
def set_phase_cache(si, phase, payload):
    _cache[cache_key(si, phase)] = (time.monotonic(), payload)
```

`orchestrator.py`(占位 v1):

```python
import asyncio, logging, time
from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
from .phases import get_pipeline_phases
from ._phase_cache import get_phase_cache, set_phase_cache
logger = logging.getLogger(__name__)

class PipelineOrchestrator:
    def __init__(self, analyzer):
        self.analyzer = analyzer
        self._log: list[dict] = []

    async def run(self, si: StockAnalysis):
        t0 = time.monotonic()
        ctx: dict = {"report_partial": [], "phase_results": {}}
        for spec in get_pipeline_phases():
            if time.monotonic() - t0 >= 300:
                logger.warning("[pipeline] 超时 300s,剩余标 超时降级"); break
            await asyncio.sleep(0)  # LLM 调用在 Task 8-13 接入
        return ctx
```

5 个 phase prompt 模板 — 每份含 `{{ stock_info }}` 占位和强制覆盖清单(全文见 spec 第 3 节),版本 v1 仅骨架。

- [ ] **Step 4:Green**

- [ ] **Step 5:提交** `git commit -m "feat(pipeline v2): 五阶段骨架 + 阶段缓存"`

---

## Task 5-10:6 个工具(独立、并行可实施)

按 TDD 三步执行,每工具独立提交。

### Task 5 `tools/technicals.py`

- 输入 `KlineData`;输出 `{bar_count, ma5/10/20/60, macd, rsi, bollinger, volume_ratio_5, main_net_Xd, trend}` 或 `{"__partial__":"<reason>"}`
- 测试 fixture:60 根 K 线(前 30 根涨,后 30 根跌,趋势转折)+ 不足 5 根 → `__partial__`

### Task 6 `tools/financial_temporal.py`

- 输入 `StockAnalysis.history_financial`;输出 `{n_years, years[], revenue_cagr, np_cagr, roe_trend, ocf_profit_ratio, break_points[]}` 或 `__partial__`
- OCF 缺失时仍返 revenue/np,ocf_ratio 字段 `__partial__`

### Task 7 `tools/peer_compare.py`

- 输入 `(name, financial)`;模型触发 `web_search("<name> 同行竞品 PE ROE")`;parse html 返竞品表;无结果 `__partial__`
- 测试 mock `execute_web_search`

### Task 8 `tools/business_moat.py`

- 输入 `(self_fin, research, social, history_ocf)`;输出 `{swot, moat_source, ocf_quality, upstream_downstream}` 或 `__partial__`。80% 内算。

### Task 9 `tools/risk_quant.py`

- 输入 `(financial_temporal输出, quote)`;输出 `{bears:[{trigger,impact_pct}], bulls:[...], ratio_alerts:{goodwill, receivables, inventory}}` 或 `__partial__`;每节数均在 spec 强制清单。

### Task 10 `tools/valuation.py`

- 输入 `(financial_temporal, peer_compare, quote)`;输出 `{pe, pb, fcfe_targets:{conservative, neutral, optimistic, assumptions}, peer_compare_table}` 或 `__partial__`

每工具提交格式:`feat(pipeline v2): <工具名> 工具`。

---

## Tasks 11-16:接入 + 正式 prompt + e2e(详见附录 plan)

详细步骤已拆至 `docs/superpowers/plans/2026-07-05-ai-pipeline-v2-tasks-11-16.md` 同步落盘。

### Task 11:`analyze(use_pipeline_v2=...)` 路由分岔

- `_legacy_analyze()` 钉死原逻辑;新入口 `_pipeline_analyze()` 调 orchestrator
- 测试:Flag 真 → 调 orchestrator;假 → 调旧逻辑(mock 验证)

### Task 12:`_pipeline_analyze()` 接 orchestrator 五阶段

- orchestrator.run(si) → AnalysisReport 转换 + disclaimer + 写 L1 磁盘缓存(复用现 `ai/cache.py` 不变)

### Task 13:PLAN / COLLECT / ANALYSIS 三阶段调 LLM + 工具组合

- 阶段 system prompt 加载 `pipeline/prompts/phase_N.md`;COLLECT 并行异步调 technicals/financial_temporal/peer_compare + at most 1 次 web_search;ANALYSIS 串行 risk_quant → valuation/business_moat 并行 + 可选 web_search

### Task 14:SELF_CHECK 阶段 — LLM 输出结构化 JSON

- 5 项校验清单:数字源标注 / 表格合规 / 触发条件 / 投资建议明确 / 无重复
- JSON schema: `{ citations_ok, tables_ok, trigger_ok, advice_ok, norepeat_ok, fixes_needed:[] }`
- 任一 false → 把 `fixes_needed` 喂回 ANALYSIS 重跑该子段(最多 1 次循环)

### Task 15:COMPILE 阶段 + 5 个 prompt 最终稿

- 复用 `_stream_final_response` 流式输出最终 Markdown
- 注入 disclaimer(现 `investment_advice` 字段不变)
- 写磁盘缓存(现 `ai/cache.py` 接口)

### Task 16:e2e 实测 600519/000001/601318 + 补充 CLI 开关 `--use-v2/--legacy`

- `aimoon 600519 --use-v2` 按 spec 11.3 验收
- 默认 CLI `aimoon <code>` 走 v2,`--legacy` 切旧链路

---

## 最终 Gate(见 spec 11.3)

- 600519/000001/601318 实跑达"分析可用":三张核心表格 100%,看空非空 100%,SELF_CHECK 平均 ≤2 次通过
- 单阶段失败不中断(pipeline 整体成功率 100%)
- `--legacy` 旧链路 100% 兼容无回归
- 6 工具单测 100% 过

---

## 与 spec 对应索引

| spec 章节 | plan 任务 |
|---|---|
| 第 3(五阶段)、第 4.1(PLAN 门) | Task 4(骨架)+ Task 13(PLAN) |
| 第 4.2-4.3(COLLECT) | Task 5,6,7 + Task 13 |
| 第 4.4(ANALYSIS) | Task 8,9,10 + Task 13 |
| 第 4.5(SELF_CHECK) | Task 14 |
| 第 4.6(COMPILE) | Task 15 |
| 第 5(六角架构落位) | Task 1,2,3,11,12 |
| 第 6(StockAnalysis 扩展) | Task 1,2,3 |
| 第 7(缓存/降级/超时) | Task 4,L2 + Task 12 + Task 14 降级 |
| 第 10(实施 3 Phase) | Tasks 1-10(P1+P2)/11-15(P3)/16(P4) |
