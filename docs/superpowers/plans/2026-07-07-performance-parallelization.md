# Performance Parallelization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce aimoon pipeline end-to-end latency by 20-50s through parallelizing independent data collection and tool execution stages.

**Architecture:** Five targeted changes to two files (`composite_repo.py` and `orchestrator.py`): social collection runs alongside other collectors, tool execution is maximally parallel, and LLM calls reuse a shared HTTP client. No changes to AI reasoning parameters.

**Tech Stack:** Python 3.12+, asyncio, httpx, pytest

## Global Constraints

- Collectors never abort the pipeline — each has mock fallback; exceptions silently caught (project convention from AGENTS.md)
- Intentional broad exceptions: `except Exception: pass` in collectors (AGENTS.md)
- Python 3.12+ syntax: use `X | Y` not `Optional[X]`
- ruff line-length 100, select E/F/I/N/W/UP
- Tests run via `uv run pytest` (project uses uv)

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/aimoon/adapters/driven/ai/pipeline/orchestrator.py` | Modify | Parallelize tool execution (peer_compare into gather, risk_quant+valuation parallel), reuse httpx client, add timing |
| `src/aimoon/adapters/driven/collectors/composite_repo.py` | Modify | Parallelize social collection with other data collectors, add timing |
| `tests/test_pipeline_phases.py` | Modify | Add/update tests for parallel tool execution |
| `tests/test_collectors.py` | Check | Verify existing collector tests still pass |

---

### Task 1: Parallelize peer_compare with other tools

**Files:**
- Modify: `src/aimoon/adapters/driven/ai/pipeline/orchestrator.py:186-205`
- Test: `tests/test_pipeline_phases.py` (existing `_fake_analyzer` fixture covers this)

**Interfaces:**
- Consumes: `_run_peer_compare(si, search_fn)` (module-level async function, returns `dict`)
- Produces: `peer` dict (same shape as before, consumed by `valuation` tool and `render_peer_comparison`)

- [ ] **Step 1: Update the tool gather block in `_phase_analysis`**

In `orchestrator.py` lines 186-205, replace the sequential peer_compare → gather → risk → val with:

```python
        else:
            # 1. 并行跑 4 个纯工具 + peer_compare(web search)
            tech_coro = _run_safe(TOOL_RUNNERS["technicals"], getattr(si, "kline", None),
                                   getattr(si, "capital_flow", None))
            fin_coro = _run_safe(TOOL_RUNNERS["financial_temporal"],
                                  getattr(si, "history_financial", None))
            moat_coro = _run_safe(
                TOOL_RUNNERS["business_moat"],
                getattr(si, "financial", None),
                getattr(si, "research", None),
                getattr(si, "social_posts", None),
                getattr(si, "history_financial", None),
            )
            peer_coro = _run_peer_compare(si, execute_web_search)
            tech, fin, moat, peer = await asyncio.gather(
                tech_coro, fin_coro, moat_coro, peer_coro,
            )
            risk, val = await asyncio.gather(
                _run_safe(TOOL_RUNNERS["risk_quant"], fin, si.quote),
                _run_safe(TOOL_RUNNERS["valuation"], fin, peer, si.quote),
            )
```

This removes the standalone `peer_raw = _run_peer_compare(...)` block (lines 198-202), the `inspect.iscoroutine` check, and the separate `gather(tech_coro, fin_coro, moat_coro)` call. It also combines risk_quant and valuation into a single gather (Task 2 merged here since they're in the same code block).

- [ ] **Step 2: Remove unused `import inspect` if no longer referenced**

Check if `inspect` is used anywhere else in `orchestrator.py`. The only usage was line 199 (`inspect.iscoroutine`). If no other usage, remove `import inspect` from the imports at line 14.

- [ ] **Step 3: Run existing tests**

Run: `uv run pytest tests/test_pipeline_phases.py -v`
Expected: `test_orchestrator_runs_two_phases` PASSES (the `_fake_analyzer` fixture monkeypatches `_run_peer_compare` to return a dict synchronously, but `_run_peer_compare` is async so the gather still works).

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest tests/ -v --timeout=30`
Expected: All tests pass. If `_fake_peer_compare_module` returns a plain dict (not a coroutine), the gather may fail — if so, update the fixture in Step 5.

- [ ] **Step 5: Fix test fixture if needed**

If the existing `_fake_peer_compare_module` fixture breaks because gather expects a coroutine, update it in `tests/test_pipeline_phases.py`:

```python
    async def _fake_peer_compare_module(si, search_fn):
        return {"_fake": True, "tool": "peer_compare"}
```

Add `async` keyword so it returns a coroutine compatible with `asyncio.gather`.

- [ ] **Step 6: Commit**

```bash
git add src/aimoon/adapters/driven/ai/pipeline/orchestrator.py tests/test_pipeline_phases.py
git commit -m "perf: parallelize peer_compare + risk_quant/valuation in tool gather"
```

---

### Task 2: Reuse httpx client for LLM calls

**Files:**
- Modify: `src/aimoon/adapters/driven/ai/pipeline/orchestrator.py:75-79` (init), `321-346` (`_call_llm_with_tools`), `348-382` (`_stream_llm`)

**Interfaces:**
- Consumes: `httpx.AsyncClient` (created once, shared across LLM calls)
- Produces: nothing new (same API responses)

- [ ] **Step 1: Add shared LLM httpx client to PipelineOrchestrator.__init__**

In `orchestrator.py`, modify `PipelineOrchestrator.__init__` (line 78-79):

```python
class PipelineOrchestrator:
    """ANALYSIS + COMPILE 两阶段 pipeline。"""

    def __init__(self, analyzer: AnalyzerRuntime) -> None:
        self.analyzer = analyzer
        # Dedicated long-timeout client for LLM calls (300s).
        # Reuses TCP/TLS connections across ANALYSIS and COMPILE phases.
        import httpx as _httpx
        self._llm_http = _httpx.AsyncClient(timeout=300.0)
```

- [ ] **Step 2: Update `_call_llm_with_tools` to reuse shared client**

Replace lines 329-342 (the `async with _httpx.AsyncClient(timeout=300.0) as http:` block):

```python
    async def _call_llm_with_tools(self, messages: list[dict], *,
                                     max_tokens: int | None = None,
                                     reasoning_effort: str = "max") -> dict:
        import httpx as _httpx
        analyzer = self.analyzer
        settings = analyzer._provided_settings or analyzer._settings
        body: dict[str, object] = {
            "model": settings.deepseek_model, "messages": messages,
            "max_tokens": max_tokens or settings.deepseek_max_tokens,
            "reasoning_effort": reasoning_effort,
        }
        with logphase(f"llm(effort={reasoning_effort}, mt={body['max_tokens']})"):
            resp = await self._llm_http.post(
                analyzer.api_url,
                headers={"Authorization": f"Bearer {analyzer.api_key}",
                         "Content-Type": "application/json"},
                json=body,
            )
        if resp.status_code >= 400:
            logger.error("[pipeline] LLM HTTP %d: %s", resp.status_code, resp.text[:500])
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]
```

- [ ] **Step 3: Update `_stream_llm` to reuse shared client**

Replace lines 352-382 (the `async with _httpx.AsyncClient(timeout=300.0) as http:` block):

```python
    async def _stream_llm(self, messages: list[dict], *,
                          max_tokens: int | None = None,
                          reasoning_effort: str = "max") -> str:
        import httpx as _httpx
        analyzer = self.analyzer
        settings = analyzer._provided_settings or analyzer._settings
        body: dict[str, object] = {
            "model": settings.deepseek_model, "messages": messages,
            "max_tokens": max_tokens or min(settings.deepseek_max_tokens, 4096),
            "stream": True,
            "reasoning_effort": reasoning_effort,
        }
        full_text: list[str] = []
        async with self._llm_http.stream(
            "POST", analyzer.api_url,
            headers={"Authorization": f"Bearer {analyzer.api_key}",
                     "Content-Type": "application/json"},
            json=body,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                    delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        full_text.append(content)
                except json.JSONDecodeError:
                    continue
        return "".join(full_text)
```

- [ ] **Step 4: Run existing tests**

Run: `uv run pytest tests/test_pipeline_phases.py -v`
Expected: All tests pass. The `_fake_analyzer` fixture monkeypatches `_call_llm_with_tools` and `_stream_llm`, so the real httpx client is never used in tests.

- [ ] **Step 5: Commit**

```bash
git add src/aimoon/adapters/driven/ai/pipeline/orchestrator.py
git commit -m "perf: reuse shared httpx client for LLM calls (avoid TCP/TLS per call)"
```

---

### Task 3: Parallelize social collection with other collectors

**Files:**
- Modify: `src/aimoon/adapters/driven/collectors/composite_repo.py:83-181`

**Interfaces:**
- Consumes: `self._social_collector.collect(symbol, name)` returns `tuple[list, list[CollectResult]]`
- Consumes: `self._fetch_quote(symbol, name)` returns `StockQuote`
- Produces: `StockAnalysis` aggregate (same shape, no interface change)

- [ ] **Step 1: Rewrite `_collect_all_inner` to parallelize social**

Replace the body of `_collect_all_inner` (lines 83-181). The key change: fetch quote first (fastest collector, <1s), then run remaining 6 collectors + social in a single gather.

```python
    async def _collect_all_inner(self, symbol: str, name: str) -> StockAnalysis:
        # Phase A: quote first (fast, needed by social for stock name)
        print(" 采集行情...")
        quote_result = await self._fetch_quote(symbol, name)
        quote = self._unwrap_quote(quote_result, symbol, name)
        stock_name = quote.name or name

        # Phase B: remaining 6 collectors + social, all parallel
        print(" 并行采集财务/K线/资金流/研报/社媒...")
        t0 = time.monotonic()
        results = await asyncio.gather(
            self._collect_financial(symbol),
            self._collect_quarterly_financial(symbol),
            self._kline_collector.fetch(symbol),
            self._capital_flow_collector.fetch(symbol),
            self._research_collector.fetch(symbol),
            self._collect_history_financial(symbol),
            self._social_collector.collect(symbol, stock_name),
            return_exceptions=True,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        financial = self._unwrap(
            results[0],
            FinancialData,
            symbol=symbol,
            platform="财务数据(年报)",
            ok=lambda d: d and d.report_period,
            msg=lambda d: f"   财务: 报告期 {d.report_period} | ROE: {d.roe}% [来源: {d.source}]",
            fail="   财务: 获取失败。",
            elapsed_ms=elapsed_ms,
        )
        quarterly = self._unwrap(
            results[1],
            QuarterlyFinancialData,
            symbol=symbol,
            platform="财务数据(季报)",
            ok=lambda d: d and d.report_period,
            msg=lambda d: (
                f"   季报: {d.report_period} | 营收 {d.revenue / 1e8:.1f}亿 ({d.revenue_yoy:+.1f}%)"
                f" [来源: {d.source}]"
            ),
            fail="   季报: 获取失败。",
            elapsed_ms=elapsed_ms,
        )
        kline = self._unwrap(
            results[2],
            KlineData,
            symbol=symbol,
            platform="K线数据",
            ok=lambda d: d and d.bars,
            msg=lambda d: f"   K线: {len(d.bars)}根 [{d.source}]",
            fail="   K线: 获取失败，技术分析将使用基础数据。",
            elapsed_ms=elapsed_ms,
        )
        capital_flow = self._unwrap(
            results[3],
            CapitalFlowData,
            symbol=symbol,
            platform="资金流向",
            ok=lambda d: d and d.source and d.source != "all_failed",
            msg=lambda d: f"   资金流: 主力5日 {d.main_net_5d / 1e8:.2f}亿 [{d.source}]",
            fail="   资金流: 获取失败。",
            elapsed_ms=elapsed_ms,
        )
        research = self._unwrap(
            results[4],
            ResearchReportData,
            symbol=symbol,
            platform="研报数据",
            ok=lambda d: d and d.total_count > 0,
            msg=lambda d: f"   研报: {d.total_count}条 [来源: {d.source}]",
            fail="   研报: 获取失败。",
            elapsed_ms=elapsed_ms,
        )
        history_raw = results[5]
        if isinstance(history_raw, Exception):
            logger.debug("[history] 历史财务采集失败: %s", history_raw)
            print("   历史财务: 获取失败")
            history = []
        elif isinstance(history_raw, list) and (
            not history_raw or isinstance(history_raw[0], FinancialData)
        ):
            history = history_raw
            print(f"   历史财务: {len(history)} 年年报")
        else:
            history = []

        # Social result is at index 6
        social_raw = results[6]
        if isinstance(social_raw, Exception):
            logger.debug("[social] 社媒采集异常: %s", social_raw)
            all_posts = []
        elif isinstance(social_raw, tuple) and len(social_raw) == 2:
            all_posts, social_results = social_raw
            self._collect_results.extend(social_results)
        else:
            all_posts = []

        return StockAnalysis(
            symbol=symbol,
            name=stock_name,
            market=resolve_market(symbol),
            quote=quote,
            financial=financial,
            quarterly_financial=quarterly,
            kline=kline,
            capital_flow=capital_flow,
            social_posts=all_posts,
            research=research,
            history_financial=history if isinstance(history, list) else [],
        )
```

- [ ] **Step 2: Run existing collector tests**

Run: `uv run pytest tests/test_collectors.py tests/test_collector_baseline.py tests/test_pipeline.py -v`
Expected: All pass. The `MockRepo` in test_pipeline.py bypasses `composite_repo.py` entirely.

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/ -v --timeout=30`
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/aimoon/adapters/driven/collectors/composite_repo.py
git commit -m "perf: parallelize social collection with other data collectors"
```

---

### Task 4: Add timing instrumentation

**Files:**
- Modify: `src/aimoon/adapters/driven/collectors/composite_repo.py` (add import + logphase around social gather)
- Modify: `src/aimoon/adapters/driven/ai/pipeline/orchestrator.py` (add logphase around tool gather)

**Interfaces:**
- Consumes: `logphase(label)` from `timing.py` (already imported in orchestrator.py)
- Produces: DEBUG log lines on `aimoon.pipeline.timing` logger

- [ ] **Step 1: Add logphase around tool execution in orchestrator.py**

In `_phase_analysis`, wrap the main gather block with `logphase`:

```python
            with logphase("tools(tech+fin+moat+peer)"):
                tech, fin, moat, peer = await asyncio.gather(
                    tech_coro, fin_coro, moat_coro, peer_coro,
                )
            with logphase("tools(risk+val)"):
                risk, val = await asyncio.gather(
                    _run_safe(TOOL_RUNNERS["risk_quant"], fin, si.quote),
                    _run_safe(TOOL_RUNNERS["valuation"], fin, peer, si.quote),
                )
```

Note: `logphase` is a synchronous `@contextmanager`, so wrapping `await` inside it is correct (it measures wall-clock time including async waits).

- [ ] **Step 2: Add logphase around social collection in composite_repo.py**

Add import at top of `composite_repo.py`:

```python
from aimoon.adapters.driven.ai.pipeline.timing import logphase
```

Then wrap the social gather in `_collect_all_inner`. Since the social collection is now inside the big gather (Task 3), add logphase around the entire Phase B gather:

```python
        with logphase("collectors(fin+kline+cf+research+history+social)"):
            results = await asyncio.gather(...)
```

- [ ] **Step 3: Verify no circular import**

The import `from aimoon.adapters.driven.ai.pipeline.timing import logphase` in `composite_repo.py` crosses from `collectors/` into `ai/pipeline/`. Check that `timing.py` doesn't import anything from `collectors/`.

Read `timing.py` — it only imports `logging`, `time`, `contextlib`, `collections.abc`. No circular dependency.

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest tests/ -v --timeout=30`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add src/aimoon/adapters/driven/collectors/composite_repo.py src/aimoon/adapters/driven/ai/pipeline/orchestrator.py
git commit -m "perf: add logphase timing around parallelized stages"
```

---

### Task 5: Lint + final verification

**Files:** None (verification only)

- [ ] **Step 1: Run ruff**

Run: `uv run ruff check src/`
Expected: No errors. If `import inspect` was removed and is unused, ruff F401 would catch it. Fix any issues.

- [ ] **Step 2: Run mypy**

Run: `uv run mypy src/aimoon/`
Expected: No new errors (existing `ignore_missing_imports` covers httpx).

- [ ] **Step 3: Run mock pipeline end-to-end**

Run: `uv run aimoon 600519 --mock`
Expected: HTML report generated successfully. Check timing log lines appear in output.

- [ ] **Step 4: Run full test suite one final time**

Run: `uv run pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 5: Final commit (if any lint fixes)**

```bash
git add -A
git commit -m "chore: lint fixes after parallelization"
```
