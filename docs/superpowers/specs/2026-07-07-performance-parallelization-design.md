# Performance Optimization: Parallel Data Collection & Tool Execution

**Date:** 2026-07-07
**Scope:** 低风险并行化 — 不改变 AI 推理参数,仅优化并发和连接复用
**Estimated saving:** 20-50s per run

## Changes

### 1. Social collection parallelized with other collectors

**File:** `src/aimoon/adapters/driven/collectors/composite_repo.py`
**Current (lines 84-167):** 7 collectors run via `asyncio.gather` (lines 86-95), then social collection runs sequentially after (line 166).
**Problem:** Social collection (Playwright-based guba/cninfo/wechat, 60s timeout each) waits for all 7 collectors to finish.
**Fix:**
1. Extract `quote.fetch()` out of the gather (it's the fastest, <1s)
2. After getting `quote.name`, launch social collection in a second gather alongside the remaining 6 collectors

```python
# Phase A: quote first (fast, <1s)
quote = await self._fetch_quote(symbol, name)
quote = self._unwrap_quote(quote_result, symbol, name)

# Phase B: remaining 6 collectors + social, all parallel
t0 = time.monotonic()
results = await asyncio.gather(
    self._collect_financial(symbol),
    self._collect_quarterly_financial(symbol),
    self._kline_collector.fetch(symbol),
    self._capital_flow_collector.fetch(symbol),
    self._research_collector.fetch(symbol),
    self._collect_history_financial(symbol),
    self._social_collector.collect(symbol, quote.name or name),
    return_exceptions=True,
)
```

The `_unwrap` indices shift: results[0-5] are the 6 collectors, results[6] is the social tuple.

**Risk:** Minimal. Quote fetch is the fastest collector. If quote fails, social gets empty name (same as current fallback).

### 2. peer_compare added to parallel gather

**File:** `src/aimoon/adapters/driven/ai/pipeline/orchestrator.py`
**Current (lines 198-203):** `peer_compare` is awaited alone before `asyncio.gather(tech_coro, fin_coro, moat_coro)`.
**Problem:** peer_compare involves a web search call (one of the slowest tools, 5-15s), and runs sequentially.
**Fix:** Move peer_compare into the gather group.

```python
# Before:
peer_raw = _run_peer_compare(si, execute_web_search)
if inspect.iscoroutine(peer_raw):
    peer = await peer_raw
else:
    peer = peer_raw
tech, fin, moat = await asyncio.gather(tech_coro, fin_coro, moat_coro)

# After:
tech, fin, moat, peer = await asyncio.gather(
    tech_coro, fin_coro, moat_coro,
    _run_peer_compare_async(si, execute_web_search),
)
```

Need a small wrapper `_run_peer_compare_async` that handles the `inspect.iscoroutine` check inside the coroutine (not outside), so it works as a gather argument.

**Risk:** None. peer_compare has no dependency on tech/fin/moat results. The downstream consumers (risk_quant needs fin, valuation needs fin+peer) still await the gather to complete.

### 3. risk_quant and valuation parallelized

**File:** `src/aimoon/adapters/driven/ai/pipeline/orchestrator.py`
**Current (lines 204-205):**
```python
risk = await _run_safe(TOOL_RUNNERS["risk_quant"], fin, si.quote)
val = await _run_safe(TOOL_RUNNERS["valuation"], fin, peer, si.quote)
```
**Problem:** These two tools are independent of each other (risk_quant needs fin, valuation needs fin+peer, but neither needs the other's output).
**Fix:**
```python
risk, val = await asyncio.gather(
    _run_safe(TOOL_RUNNERS["risk_quant"], fin, si.quote),
    _run_safe(TOOL_RUNNERS["valuation"], fin, peer, si.quote),
)
```

**Risk:** None. Both tools are pure functions of their inputs.

### 4. Reuse shared httpx client for LLM calls

**File:** `src/aimoon/adapters/driven/ai/pipeline/orchestrator.py`
**Current (line 338):** `_call_llm_with_tools` creates a new `httpx.AsyncClient` per call:
```python
async with _httpx.AsyncClient(timeout=300.0) as http:
```
**Problem:** TCP/TLS handshake overhead on every LLM call (1-3s per call).
**Fix:** Reuse `self.analyzer._http` which is already a shared client:
```python
http = self.analyzer._http
resp = await http.post(analyzer.api_url, ...)
```

Same change in `_stream_llm` (line 362).

**Constraint:** The shared client must have timeout >= 300s. Check `adapters/driven/ai/analyzer.py` for current timeout config. If the shared client has a shorter timeout, create a dedicated long-timeout client in `PipelineOrchestrator.__init__`.

### 5. Add timing instrumentation

**Files:** `composite_repo.py`, `orchestrator.py`

Add `logphase` calls at:
- Social collection in `composite_repo.py` (around the social gather call)
- Tool execution phase in `orchestrator.py` (around the main gather)
- risk_quant+valuation gather in `orchestrator.py`

The `logphase` context manager from `timing.py` already exists and logs to `aimoon.pipeline.timing` logger.

## Testing

- `aimoon 600519 --mock` should produce identical output structure
- `aimoon 600519 --test` should complete faster (measure with timing logs)
- Compare timing logs before/after to verify parallelization gains
