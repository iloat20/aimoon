# 性能优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 减少 aimoon 单次分析总耗时，通过并行化、连接复用、缓存消除不必要的等待。

**Architecture:** 在现有六边形架构内优化，不改变分层结构。核心策略：(1) 最大化采集并发 (2) HTTP 连接复用 (3) 同步 IO 异步化 (4) 结果缓存。

**Tech Stack:** Python 3.12+, httpx, asyncio, akshare, pysnowball, playwright

---

## 性能优化概览

| 优化项 | 当前问题 | 优化方案 | 预估节省 |
|--------|----------|----------|----------|
| P1: 采集阶段全并行 | Quote 先串行，其他并行 | 所有采集器一次性 gather | ~1-2s |
| P2: AkshareFinancialAdapter 并行 | 3 张表串行调用 | `asyncio.gather` 并行 | ~1-2s |
| P3: CapitalFlow 子采集器并行 | pysnowball→akshare→northbound 串行 | 全部 gather | ~0.5-1s |
| P4: HTTP 连接池复用 | 每次请求新建客户端 | 共享 `httpx.AsyncClient` | ~200-500ms |
| P5: AI 工具调用缓存 | 相同查询重复搜索 | LRU 缓存搜索结果 | ~1-3s |
| P6: Quote 缓存 | 每次重新请求 | 磁盘 TTL 缓存 | ~200ms |
| P7: Bug 修复 | `_fetch_via_akshare` 重复定义 | 删除重复方法 | 正确性 |

---

### Task 1: 修复 `_fetch_via_akshare` 重复定义

**Files:**
- Modify: `adapters/driven/collectors/capital_flow.py:239-255`

- [ ] **Step 1: 删除重复的方法定义**

删除 `capital_flow.py` 中第二个 `_fetch_via_akshare` 方法（第 239-255 行）。第一个定义（第 99-116 行）是正确的实现，第二个覆盖了第一个但缺少错误处理。

删除从 `# ---------- pysnowball fallback ----------` 注释到文件末尾的整个代码块。

- [ ] **Step 2: 验证**

```bash
uv run python -c "from aimoon.adapters.driven.collectors.capital_flow import CapitalFlowCollector; print('OK')"
```

Expected: 无导入错误

- [ ] **Step 3: Commit**

```bash
git add adapters/driven/collectors/capital_flow.py
git commit -m "fix: remove duplicate _fetch_via_akshare method"
```

---

### Task 2: HTTP 连接池共享

**Files:**
- Modify: `adapters/driven/collectors/quote.py`
- Modify: `adapters/driven/collectors/kline.py`
- Modify: `adapters/driven/collectors/capital_flow.py`
- Modify: `adapters/driven/ai/analyzer.py`
- Modify: `adapters/driven/ai/web_search_tool.py`
- Modify: `adapters/driven/collectors/composite_repo.py`
- Modify: `adapters/driving/cli/pipeline.py`

- [ ] **Step 1: 修改 QuoteCollector 接受外部 httpx 客户端**

在 `adapters/driven/collectors/quote.py` 中：

```python
class QuoteCollector(DataCollector[StockQuote]):
    name = "quote"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client_provided = client is not None
        self._client = client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=10.0,
                limits=httpx.Limits(max_keepalive_connections=5),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client_provided:
            await self._client.aclose()
            self._client = None
```

- [ ] **Step 2: 修改 KlineCollector 接受外部 httpx 客户端**

在 `adapters/driven/collectors/kline.py` 中修改 `__init__`：

```python
class KlineCollector(DataCollector[KlineData]):
    name = "kline"

    def __init__(self, days: int = 180, client: httpx.AsyncClient | None = None) -> None:
        self._days = days
        self._client_provided = client is not None
        self._client = client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client
```

修改 `_fetch_tencent` 不再使用 `async with httpx.AsyncClient(...)`，改用 `await self._get_client()`。

- [ ] **Step 3: 修改 CapitalFlowCollector 接受外部 httpx 客户端**

在 `adapters/driven/collectors/capital_flow.py` 中修改 `__init__`：

```python
class CapitalFlowCollector(DataCollector[CapitalFlowData]):
    name = "fund_flow"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._sources_ok: list[str] = []
        self._client_provided = client is not None
        self._client = client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client
```

修改 `_em_northbound` 从 sync 改为 async（使用 `await self._get_client()` 和 `await client.get(...)`）。同时更新 `_fetch_northbound` 中的调用（不再需要 `asyncio.to_thread`）。

- [ ] **Step 4: 修改 DeepSeekAIAnalyzer 使用共享 httpx 客户端**

在 `adapters/driven/ai/analyzer.py` 的 `__init__` 中添加 `http_client` 参数并保存为 `self._http`。修改 `_call_with_tools` 和 `_stream_final_response` 使用 `self._http` 而非每次创建新 client。

- [ ] **Step 5: 修改 web_search_tool 使用共享客户端**

在 `adapters/driven/ai/web_search_tool.py` 中添加模块级共享客户端：

```python
_search_client: httpx.AsyncClient | None = None

def _get_search_client() -> httpx.AsyncClient:
    global _search_client
    if _search_client is None:
        _search_client = httpx.AsyncClient(
            timeout=10.0, follow_redirects=True,
            limits=httpx.Limits(max_keepalive_connections=5),
        )
    return _search_client
```

修改 `_search_bing` 和 `_search_ddg` 使用 `_get_search_client()` 而非 `async with httpx.AsyncClient(...)`。

- [ ] **Step 6: 修改 CompositeRepository 接受共享客户端**

在 `CompositeStockAnalysisRepository.__init__` 中添加 `http_client` 参数，传递给 QuoteCollector、KlineCollector、CapitalFlowCollector。

- [ ] **Step 7: 修改 PipelineOrchestrator 管理客户端生命周期**

在 `PipelineOrchestrator.run` 中使用 `async with httpx.AsyncClient(...)` 创建共享客户端，传递给 repo 和 ai_analyzer。

- [ ] **Step 8: 验证**

```bash
uv run python -m aimoon 600519 --mock
```

- [ ] **Step 9: Commit**

```bash
git add adapters/driven/collectors/quote.py adapters/driven/collectors/kline.py adapters/driven/collectors/capital_flow.py adapters/driven/ai/analyzer.py adapters/driven/ai/web_search_tool.py adapters/driven/collectors/composite_repo.py adapters/driving/cli/pipeline.py
git commit -m "perf: share httpx.AsyncClient connection pool across all HTTP clients"
```

---

### Task 3: 采集阶段全并行

**Files:**
- Modify: `adapters/driven/collectors/composite_repo.py`

- [ ] **Step 1: 替换 `_collect_all_inner` 方法**

将先串行 quote 再并行其他，改为所有 6 个采集器一次性 `asyncio.gather`：

```python
    async def _collect_all_inner(self, symbol: str, name: str) -> StockAnalysis:
        print(" 并行采集行情/财务/K线/资金流/研报...")
        t0 = time.monotonic()
        results = await asyncio.gather(
            self._fetch_quote(symbol, name),
            self._collect_financial(symbol),
            self._collect_quarterly_financial(symbol),
            self._kline_collector.fetch(symbol),
            self._capital_flow_collector.fetch(symbol),
            self._research_collector.fetch(symbol),
            return_exceptions=True,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        quote = self._unwrap_quote(results[0], symbol, name)
        financial = self._unwrap(results[1], FinancialData, symbol=symbol,
            platform="财务数据(年报)", ok=lambda d: d and d.report_period,
            msg=lambda d: f"   财务: 报告期 {d.report_period} | ROE: {d.roe}% [来源: {d.source}]",
            fail="   财务: 获取失败。", elapsed_ms=elapsed_ms)
        quarterly = self._unwrap(results[2], QuarterlyFinancialData, symbol=symbol,
            platform="财务数据(季报)", ok=lambda d: d and d.report_period,
            msg=lambda d: (f"   季报: {d.report_period} | 营收 {d.revenue / 1e8:.1f}亿 ({d.revenue_yoy:+.1f}%)"
                          f" [来源: {d.source}]"),
            fail="   季报: 获取失败。", elapsed_ms=elapsed_ms)
        kline = self._unwrap(results[3], KlineData, symbol=symbol,
            platform="K线数据", ok=lambda d: d and d.bars,
            msg=lambda d: f"   K线: {len(d.bars)}根 [{d.source}]",
            fail="   K线: 获取失败，技术分析将使用基础数据。", elapsed_ms=elapsed_ms)
        capital_flow = self._unwrap(results[4], CapitalFlowData, symbol=symbol,
            platform="资金流向", ok=lambda d: d and d.source and d.source != "all_failed",
            msg=lambda d: f"   资金流: 主力5日 {d.main_net_5d / 1e8:.2f}亿 [{d.source}]",
            fail="   资金流: 获取失败。", elapsed_ms=elapsed_ms)
        research = self._unwrap(results[5], ResearchReportData, symbol=symbol,
            platform="研报数据", ok=lambda d: d and d.total_count > 0,
            msg=lambda d: f"   研报: {d.total_count}条 [来源: {d.source}]",
            fail="   研报: 获取失败。", elapsed_ms=elapsed_ms)

        all_posts, social_results = await self._social_collector.collect(symbol, quote.name or name)
        self._collect_results.extend(social_results)

        return StockAnalysis(
            symbol=symbol, name=quote.name or name, market=resolve_market(symbol),
            quote=quote, financial=financial, quarterly_financial=quarterly,
            kline=kline, capital_flow=capital_flow, social_posts=all_posts, research=research,
        )
```

- [ ] **Step 2: 添加 `_fetch_quote` 和 `_unwrap_quote` 方法**

```python
    async def _fetch_quote(self, symbol: str, name: str) -> StockQuote:
        """纯采集，不打印不追踪结果。"""
        return await self._quote_collector.fetch(symbol, name=name)

    def _unwrap_quote(self, result: object, symbol: str, name: str) -> StockQuote:
        """解包 quote 结果，打印状态，记录 CollectResult。"""
        if isinstance(result, Exception):
            print(f"   行情: 获取失败 [{type(result).__name__}]")
            self._collect_results.append(
                CollectResult(platform="实时行情", status="failed", count=0, elapsed_ms=0, error=str(result))
            )
            return StockQuote(symbol=symbol, name=name, source="获取失败")
        quote = result
        if quote and quote.price > 0:
            info = f"{quote.name}: {quote.price} ({quote.change_pct:+.2f}%) PE={quote.pe}"
            print(f"   {info} [来源: {quote.source}]")
            self._collect_results.append(
                CollectResult(platform="实时行情", status="success", count=1, elapsed_ms=0)
            )
            return quote
        print("   行情: 获取失败（价格为零）")
        self._collect_results.append(
            CollectResult(platform="实时行情", status="failed", count=0, elapsed_ms=0, error="价格为零")
        )
        return StockQuote(symbol=symbol, name=name, source="获取失败")
```

- [ ] **Step 3: 删除旧的 `_collect_quote` 方法**

原 `_collect_quote` 已被替代，删除之。

- [ ] **Step 4: 验证**

```bash
uv run python -m aimoon 600519 --mock
```

- [ ] **Step 5: Commit**

```bash
git add adapters/driven/collectors/composite_repo.py
git commit -m "perf: parallelize all 6 data collectors in single asyncio.gather"
```

---

### Task 4: AkshareFinancialAdapter 3 表并行

**Files:**
- Modify: `adapters/driven/financial/akshare_adapter.py`

- [ ] **Step 1: 修改 `_fetch_all` 为 async 并并行获取三张表**

```python
    async def fetch(self, symbol: str, **kwargs: Any) -> FinancialData:
        """Fetch financial data for a symbol."""
        report_type = kwargs.get("report_type", "年报")
        cache_key = f"financial:{symbol}:{report_type}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            if isinstance(cached, dict) and cached.get("_empty"):
                return FinancialData(symbol=symbol, source="akshare_cache_empty")
            return FinancialData.model_validate(cached)

        try:
            result = await self._fetch_all(symbol, report_type)
        except Exception as e:
            logger.warning("[akshare] fetch failed for %s: %s", symbol, e)
            return FinancialData(symbol=symbol, source=f"akshare_failed: {e}")

        if result.source.startswith("akshare_empty"):
            self._cache.set(cache_key, {"_empty": True})
        else:
            self._cache.set(cache_key, result.model_dump())

        return result

    async def _fetch_all(self, symbol: str, report_type: str = "年报") -> FinancialData:
        """Fetch and merge data from all three financial statements in parallel."""
        result = FinancialData(symbol=symbol, source="akshare(东方财富)")
        prefix = "SH" if symbol.startswith("6") else "SZ" if symbol.startswith("0") else "BJ"
        ak_symbol = f"{prefix}{symbol}"

        loop = asyncio.get_running_loop()
        income_df, bs_df, cf_df = await asyncio.gather(
            loop.run_in_executor(None, self._sync_income, ak_symbol, report_type),
            loop.run_in_executor(None, self._sync_balance, ak_symbol, report_type),
            loop.run_in_executor(None, self._sync_cashflow, ak_symbol, report_type),
            return_exceptions=True,
        )

        if isinstance(income_df, pd.DataFrame) and not income_df.empty:
            self._parse_income_statement(result, income_df)
        if isinstance(bs_df, pd.DataFrame) and not bs_df.empty:
            self._parse_balance_sheet(result, bs_df)
        if isinstance(cf_df, pd.DataFrame) and not cf_df.empty:
            self._parse_cash_flow(result, cf_df)

        if result.revenue == 0 and result.net_profit == 0 and result.total_assets == 0:
            result.source = "akshare_empty"
        if result.net_profit != 0 and result.equity > 0:
            result.roe = round(result.net_profit / result.equity * 100, 2)

        return result

    def _sync_income(self, ak_symbol: str, report_type: str):
        """同步获取利润表（在线程池中运行）。"""
        import akshare as ak
        df = ak.stock_profit_sheet_by_report_em(symbol=ak_symbol)
        if df is not None and not df.empty:
            return _filter_report_type(df, report_type)
        return None

    def _sync_balance(self, ak_symbol: str, report_type: str):
        """同步获取资产负债表。"""
        import akshare as ak
        df = ak.stock_balance_sheet_by_report_em(symbol=ak_symbol)
        if df is not None and not df.empty:
            return _filter_report_type(df, report_type)
        return None

    def _sync_cashflow(self, ak_symbol: str, report_type: str):
        """同步获取现金流表。"""
        import akshare as ak
        df = ak.stock_cash_flow_sheet_by_report_em(symbol=ak_symbol)
        if df is not None and not df.empty:
            return _filter_report_type(df, report_type)
        return None
```

- [ ] **Step 2: 验证**

```bash
uv run python -c "
import asyncio
from aimoon.adapters.driven.financial.akshare_adapter import AkshareFinancialAdapter
async def test():
    adapter = AkshareFinancialAdapter()
    result = await adapter.fetch('600519')
    print(f'营收: {result.revenue}, ROE: {result.roe}%, 来源: {result.source}')
asyncio.run(test())
"
```

- [ ] **Step 3: Commit**

```bash
git add adapters/driven/financial/akshare_adapter.py
git commit -m "perf: parallelize 3 financial statement API calls with asyncio.gather"
```

---

### Task 5: CapitalFlow 子采集器全并行

**Files:**
- Modify: `adapters/driven/collectors/capital_flow.py`

- [ ] **Step 1: 修改 `fetch` 方法并行所有子采集器**

```python
    async def fetch(self, symbol: str, **kwargs: Any) -> CapitalFlowData:
        """Run sub-fetchers with smart fallback; return aggregated CapitalFlowData."""
        data = CapitalFlowData(symbol=symbol)
        sources: list[str] = []

        # 1. 先运行 pysnowball（主源）
        await self._fetch_via_pysnowball(symbol, data, sources)

        # 2. 并行运行：akshare（fallback）+ northbound + lhb
        results = await asyncio.gather(
            self._fetch_via_akshare(symbol, data, sources),
            self._fetch_northbound(symbol, data, sources),
            self._fetch_lhb(symbol, data, sources),
            return_exceptions=True,
        )

        for r in results:
            if isinstance(r, Exception):
                logging.warning("[capital_flow_subfetch] %s: %s", type(r).__name__, r)

        data.source = "+".join(sources) if sources else "all_failed"
        return data
```

- [ ] **Step 2: 验证**

```bash
uv run python -m aimoon 600519 --mock
```

- [ ] **Step 3: Commit**

```bash
git add adapters/driven/collectors/capital_flow.py
git commit -m "perf: parallelize capital flow sub-fetchers with asyncio.gather"
```

---

### Task 6: AI 工具调用结果缓存

**Files:**
- Modify: `adapters/driven/ai/web_search_tool.py`

- [ ] **Step 1: 添加搜索结果缓存**

在 `adapters/driven/ai/web_search_tool.py` 中添加：

```python
import hashlib
import time

_search_cache: dict[str, tuple[float, str]] = {}
_SEARCH_CACHE_TTL = 300  # 5 分钟


def _get_cached_search(query: str) -> str | None:
    key = hashlib.md5(query.encode()).hexdigest()
    if key in _search_cache:
        ts, result = _search_cache[key]
        if time.time() - ts < _SEARCH_CACHE_TTL:
            return result
        del _search_cache[key]
    return None


def _set_cached_search(query: str, result: str) -> None:
    key = hashlib.md5(query.encode()).hexdigest()
    _search_cache[key] = (time.time(), result)
    if len(_search_cache) > 100:
        _search_cache.clear()
```

- [ ] **Step 2: 修改 `execute_web_search` 使用缓存**

```python
async def execute_web_search(query: str, max_results: int = 5) -> str:
    """Execute a web search with fallback: Bing → DuckDuckGo. Results are cached."""
    cached = _get_cached_search(query)
    if cached is not None:
        return cached

    result = await _search_bing(query, max_results)
    if not result:
        result = await _search_ddg(query, max_results)
    if not result:
        result = "搜索失败: 所有搜索引擎均不可用"

    _set_cached_search(query, result)
    return result
```

- [ ] **Step 3: 验证**

```bash
uv run python -m aimoon 600519 --mock
```

- [ ] **Step 4: Commit**

```bash
git add adapters/driven/ai/web_search_tool.py
git commit -m "perf: add LRU cache for web search results to avoid duplicate queries"
```

---

### Task 7: Quote 磁盘缓存

**Files:**
- Modify: `adapters/driven/collectors/quote.py`

- [ ] **Step 1: 添加 Quote 缓存**

在 `adapters/driven/collectors/quote.py` 中：

```python
from aimoon.adapters.driven.common.cache import DiskTtlCache

_quote_cache = DiskTtlCache(namespace="quote", ttl_seconds=60)  # 1 分钟 TTL


class QuoteCollector(DataCollector[StockQuote]):
    ...
    async def fetch(self, symbol: str, **kwargs: Any) -> StockQuote:
        """Fetch quote with caching. Cache hit avoids HTTP requests entirely."""
        cached = _quote_cache.get(f"quote:{symbol}")
        if cached is not None:
            return StockQuote.model_validate(cached)

        result = await self._fetch_uncached(symbol, **kwargs)
        if result and result.price > 0:
            _quote_cache.set(f"quote:{symbol}", result.model_dump())
        return result

    async def _fetch_uncached(self, symbol: str, **kwargs: Any) -> StockQuote:
        """原始 fetch 逻辑（无缓存）。"""
        name = kwargs.pop("name", "")
        # ... 原来的 fetch 逻辑搬到这里
```

- [ ] **Step 2: 验证**

```bash
uv run python -m aimoon 600519 --mock
```

- [ ] **Step 3: Commit**

```bash
git add adapters/driven/collectors/quote.py
git commit -m "perf: add disk TTL cache for quote data (1 min TTL)"
```

---

### Task 8: `retry_on_connection` 异步化

**Files:**
- Modify: `adapters/driven/common/retry.py`

- [ ] **Step 1: 添加 async 版重试函数**

```python
async def async_retry_on_connection(func, *args, retries: int = 2, delay: float = 1.0, **kwargs):
    """Async version: use asyncio.sleep instead of time.sleep."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return func(*args, **kwargs)
        except (ConnectionError, ConnectionAbortedError, TimeoutError, OSError) as exc:
            last_exc = exc
            if attempt < retries:
                logging.debug("[retry] %s attempt %d/%d failed: %s",
                              func.__qualname__, attempt + 1, retries, exc)
                await asyncio.sleep(delay * (attempt + 1))
    assert last_exc is not None
    raise last_exc
```

- [ ] **Step 2: 验证**

```bash
uv run python -c "from aimoon.adapters.driven.common.retry import async_retry_on_connection; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add adapters/driven/common/retry.py
git commit -m "perf: add async_retry_on_connection to avoid blocking event loop"
```

---

## 验证清单

每完成一个 Task 后，运行以下命令确认无回归：

```bash
# Lint + type check
uv run ruff check src/
uv run mypy src/aimoon/

# 功能验证（mock 模式，无需 API key）
uv run python -m aimoon 600519 --mock
```

全部完成后，用真实数据测试一次完整流程：

```bash
time uv run python -m aimoon 600519 --test
```

对比优化前后的总耗时。

---

## 预期效果

| 阶段 | 优化前 | 优化后 | 节省 |
|------|--------|--------|------|
| 采集阶段 | ~3-5s（串行 quote + 并行其他） | ~2-3s（全并行） | ~1-2s |
| 财务数据 | ~2-3s（3 次串行 API） | ~1-2s（并行） | ~1s |
| 资金流 | ~1-2s（串行子采集） | ~0.5-1s（并行） | ~0.5-1s |
| HTTP 连接 | ~200-500ms（多次 TCP 握手） | ~50-100ms（连接复用） | ~150-400ms |
| AI 工具调用 | ~3-10s（多轮重复搜索） | ~2-5s（缓存命中） | ~1-5s |
| **总计** | **~10-20s** | **~6-12s** | **~30-50%** |
