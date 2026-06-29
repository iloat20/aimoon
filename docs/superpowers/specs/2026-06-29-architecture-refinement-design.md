# 架构优化设计：六边形精练

**日期**: 2026-06-29
**状态**: 待实施
**版本**: v0.4.0 → v0.5.0（架构优化，外部兼容）

## 1. 背景与目标

### 1.1 当前问题

aimoon v0.4.0 已完成六边形架构重构，分层清晰，但存在以下架构痛点：

| 问题 | 影响 |
|------|------|
| `CompositeRepository`（249行）混合编排、错误处理、状态追踪、输出 | SRP 违反，难以单元测试 |
| `SocialMediaOrchestrator` 模块级可变单例（`_pw_instance/_pw_browser`） | 全局状态，无法注入 mock |
| `DeepSeekAIAnalyzer`（530行）混合 HTTP、prompt、工具调用、输出处理 | 核心逻辑无法独立测试 |
| `StockAnalysis` 13+ 字段，每加维度需改 | 扩展性差 |
| `get_collect_results()` 可变状态模式 | 时序依赖，非线程安全 |
| 采集器直接 `print()` | 输出无法断言 |
| 无 DI 容器，PipelineOrchestrator 手动 new 所有适配器 | 组装逻辑硬编码 |
| 无测试 | 回归风险高 |

### 1.2 优化目标

1. **可测试性** — 采集器和报告生成器可独立单测
2. **职责分离** — 每个组件单一职责
3. **外部兼容** — CLI 命令、报告格式、.env 配置完全不变
4. **零新依赖** — 全部用标准库 + 已有依赖

## 2. 设计方案

### 2.1 依赖注入容器

新增 `core/application/container.py`：

```python
class Container:
    """手动 DI 容器 — 无魔法，纯 Python，~50 行。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._singletons: dict[type, object] = {}
        self._overrides: dict[type, object] = {}

    def resolve(self, cls: type[T]) -> T:
        """解析依赖，自动管理单例。测试时可用 override() 替换。"""
        if cls in self._overrides:
            return self._overrides[cls]  # type: ignore[return-value]
        if cls not in self._singletons:
            self._singletons[cls] = self._create(cls)
        return self._singletons[cls]  # type: ignore[return-value]

    def override(self, cls: type, instance: object) -> None:
        """测试时替换实现。"""
        self._overrides[cls] = instance
        self._singletons.pop(cls, None)

    def _create(self, cls: type[T]) -> T:
        """根据类型创建实例 — 硬编码工厂，无反射。"""
        # 各类型的创建逻辑
        ...
```

**设计决策**：不用 `dependency-injector` 库。
- 项目依赖越少越好
- 测试时 `override()` 比配置容器更直观
- 类型安全（泛型 `T`）
- 无反射，IDE 可追踪

### 2.2 ProgressReporter Protocol

替换所有 `print()` 调用：

```python
class ProgressReporter(Protocol):
    """进度报告接口 — 采集器和编排器依赖此协议。"""

    def report(self, message: str, *, level: str = "info") -> None:
        """报告一条消息。level: info/warning/success."""

    def progress(self, stage: str, *, current: int, total: int) -> None:
        """报告进度。stage 如 '采集行情'、'采集K线'."""
```

**实现**：

| 实现 | 用途 |
|------|------|
| `CliProgressReporter` | 生产环境，保持现有 print 行为 |
| `NullProgressReporter` | 测试环境，静默 |
| `RecordingProgressReporter` | 测试环境，记录所有调用供断言 |

### 2.3 HttpClient 抽象

```python
@dataclass(frozen=True)
class HttpResponse:
    """HTTP 响应值对象。"""
    status_code: int
    text: str

    def json(self) -> Any:
        return json.loads(self.text)


class HttpClient(Protocol):
    """HTTP 客户端抽象 — 采集器依赖此接口。"""
    async def get(self, url: str, **kwargs: Any) -> HttpResponse: ...
    async def post(self, url: str, **kwargs: Any) -> HttpResponse: ...
    async def aclose(self) -> None: ...


class HttpxClient:
    """生产实现 — 包装 httpx.AsyncClient。"""
    def __init__(self, timeout: float = 30.0) -> None:
        self._client = httpx.AsyncClient(timeout=timeout)

    async def get(self, url: str, **kwargs: Any) -> HttpResponse:
        resp = await self._client.get(url, **kwargs)
        return HttpResponse(status_code=resp.status_code, text=resp.text)

    async def post(self, url: str, **kwargs: Any) -> HttpResponse:
        resp = await self._client.post(url, **kwargs)
        return HttpResponse(status_code=resp.status_code, text=resp.text)

    async def aclose(self) -> None:
        await self._client.aclose()
```

**测试实现**：

```python
class FakeHttpClient:
    """测试用 — 根据 URL 模式返回预设响应。"""

    def __init__(self) -> None:
        self._responses: dict[str, HttpResponse] = {}
        self.calls: list[tuple[str, str]] = []  # (method, url)

    def add_response(self, url_pattern: str, response: HttpResponse) -> None:
        self._responses[url_pattern] = response

    async def get(self, url: str, **kwargs: Any) -> HttpResponse:
        self.calls.append(("get", url))
        for pattern, resp in self._responses.items():
            if pattern in url:
                return resp
        raise ConnectionError(f"No mock for {url}")

    async def post(self, url: str, **kwargs: Any) -> HttpResponse:
        self.calls.append(("post", url))
        for pattern, resp in self._responses.items():
            if pattern in url:
                return resp
        raise ConnectionError(f"No mock for {url}")

    async def aclose(self) -> None:
        pass
```

### 2.4 BrowserFactory 抽象

```python
class BrowserFactory(Protocol):
    """浏览器工厂 — 替换模块级 Playwright 单例。"""
    async def acquire(self) -> Any: ...
    async def release(self, browser: Any) -> None: ...
    async def shutdown(self) -> None: ...


class PlaywrightBrowserFactory:
    """生产实现 — 内部管理 Playwright 单例。"""

    def __init__(self) -> None:
        self._pw_instance: Any | None = None
        self._browser: Any | None = None
        self._lock: asyncio.Lock | None = None

    async def acquire(self) -> Any:
        if self._browser is not None:
            return self._browser
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._browser is not None:
                return self._browser
            from playwright.async_api import async_playwright
            self._pw_instance = await async_playwright().start()
            self._browser = await self._pw_instance.chromium.launch(headless=True)
            return self._browser

    async def release(self, browser: Any) -> None:
        pass  # 保持热启动

    async def shutdown(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw_instance is not None:
            await self._pw_instance.stop()
            self._pw_instance = None
```

### 2.5 CompositeRepository 拆分

#### 2.5.1 瘦仓库

```python
class CompositeStockAnalysisRepository(StockAnalysisRepository):
    """瘦仓库 — 只做组合查询，~50 行。"""

    def __init__(self, orchestrator: CollectorOrchestrator) -> None:
        self._orchestrator = orchestrator

    async def collect_all(self, symbol: str, name: str = "") -> StockAnalysis:
        payload = await self._orchestrator.orchestrate(symbol, name)
        return payload.stock_analysis

    async def get_collect_results(self) -> list[CollectResult]:
        """兼容旧接口 — 委托给 orchestrator 的最新结果。"""
        return list(self._orchestrator.last_results)
```

#### 2.5.2 编排器

```python
@dataclass(frozen=True)
class CollectPayload:
    """采集阶段产出 — 不可变。"""
    stock_analysis: StockAnalysis
    results: tuple[CollectResult, ...]
    elapsed_ms: int


class CollectorOrchestrator:
    """编排器 — 管理并发、错误处理、结果聚合。"""

    def __init__(
        self,
        quote: QuoteCollector,
        financial: FinancialCollector,
        kline: KlineCollector,
        capital_flow: CapitalFlowCollector,
        research: ResearchReportCollector,
        social: SocialMediaOrchestrator,
        reporter: ProgressReporter,
    ) -> None:
        self._quote = quote
        self._financial = financial
        self._kline = kline
        self._capital_flow = capital_flow
        self._research = research
        self._social = social
        self._reporter = reporter
        self._last_results: tuple[CollectResult, ...] = ()

    @property
    def last_results(self) -> tuple[CollectResult, ...]:
        return self._last_results

    async def orchestrate(self, symbol: str, name: str = "") -> CollectPayload:
        """执行完整编排流程。"""
        ...
```

#### 2.5.3 接口变更

```python
# 之前
stock = await repo.collect_all(symbol, name)
results = await repo.get_collect_results()  # 第二次调用

# 之后（通过 orchestrator）
payload = await orchestrator.orchestrate(symbol, name)
stock = payload.stock_analysis
results = payload.results
```

**兼容性**：`CompositeStockAnalysisRepository` 保留 `get_collect_results()` 委托给 `orchestrator.last_results`，旧代码无需改动。

### 2.6 AI Analyzer 拆分

```
DeepSeekAIAnalyzer (门面 ~80行)
├── DeepSeekApiClient      — HTTP + SSE + 工具调用 (~150行)
├── PromptBuilder          — StockAnalysis → user message (~120行)
└── PostProcessor          — 摘要/清洗/修正 (~80行)
```

#### 2.6.1 PromptBuilder（纯函数）

```python
@dataclass(frozen=True)
class PromptContext:
    """Prompt 构建的输入 — 扁平化数据。"""
    symbol: str
    name: str
    quote: dict[str, Any]
    financial: dict[str, Any]
    quarterly_financial: dict[str, Any]
    capital_flow: dict[str, Any]
    social: dict[str, str]  # platform -> text
    kline_summary: dict[str, Any] | None
    current_time: str


class PromptBuilder:
    """纯函数 — 无 IO，可直接单测。"""

    def build_system_prompt(self) -> str:
        return SYSTEM_PROMPT

    def build_user_prompt(self, ctx: PromptContext) -> str:
        """构建用户消息。"""
        ...

    def build_context(self, info: StockAnalysis) -> PromptContext:
        """从 StockAnalysis 提取 PromptContext。"""
        ...
```

#### 2.6.2 DeepSeekApiClient

```python
class DeepSeekApiClient:
    """仅负责 HTTP 通信 — 可注入 mock transport。"""

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def call_with_tools(
        self, messages: list[dict], tools: list[dict]
    ) -> tuple[list[dict], bool] | None:
        """发送非流式请求，处理工具调用。"""
        ...

    async def stream_response(self, messages: list[dict]) -> str:
        """发送流式请求，返回完整文本。"""
        ...
```

#### 2.6.3 PostProcessor（纯函数）

```python
class PostProcessor:
    """纯函数 — 无 IO，可直接单测。"""

    def process(self, raw_text: str, current_price: float | None) -> AnalysisReport:
        """处理 AI 输出：摘要提取、支撑位修正。"""
        ...

    def extract_summary(self, md_text: str, max_len: int = 200) -> str:
        """提取摘要。"""
        ...

    def sanitize_support_resistance(
        self, report: AnalysisReport, current_price: float | None
    ) -> AnalysisReport:
        """支撑位/阻力位修正。"""
        ...
```

### 2.7 StockAnalysis 瘦身

```python
class StockAnalysis(BaseModel):
    """核心字段必填，扩展字段可选。"""

    # === 核心标识 ===
    symbol: str
    name: str = ""
    market: str = ""
    collected_at: datetime = Field(default_factory=datetime.now)

    # === 主要数据（Optional 允许部分失败）===
    quote: StockQuote | None = None
    financial: FinancialData | None = None
    quarterly_financial: QuarterlyFinancialData | None = None
    kline: KlineData | None = None
    capital_flow: CapitalFlowData | None = None
    research: ResearchReportData | None = None
    social_posts: tuple[SocialPost, ...] = ()  # 不可变

    # === 扩展数据（用 dict 容纳未来新增）===
    extensions: dict[str, BaseModel] = Field(default_factory=dict)

    def get_extension(self, key: str, cls: type[T]) -> T | None:
        """安全获取扩展数据。"""
        val = self.extensions.get(key)
        if isinstance(val, cls):
            return val
        return None

    def has_data(self, field_name: str) -> bool:
        """检查某字段是否有有效数据。"""
        val = getattr(self, field_name, None)
        if val is None:
            return False
        if hasattr(val, "is_valid"):
            return val.is_valid()  # type: ignore[no-any-return]
        return True
```

**兼容性保障**：
- 旧代码 `info.quote` → 新代码 `info.quote or StockQuote(symbol=...)`
- 报告生成器用 `getattr(info, "quote", None)` 防御
- 模板中 `quote.price` → `quote.price`（模板自动处理 None）

## 3. 文件变更清单

### 3.1 新增文件

```
core/application/container.py          # DI 容器
core/application/progress.py           # ProgressReporter Protocol + 实现
core/application/http_client.py        # HttpClient Protocol + HttpxClient
core/application/browser_factory.py    # BrowserFactory Protocol + 实现
core/application/prompt_context.py     # PromptContext 值对象
adapters/driven/collectors/orchestrator.py  # CollectorOrchestrator
adapters/driven/ai/prompt_builder.py   # PromptBuilder
adapters/driven/ai/post_processor.py   # PostProcessor
adapters/driven/ai/api_client.py       # DeepSeekApiClient
tests/                                 # 测试目录
```

### 3.2 修改文件

```
adapters/driven/collectors/composite_repo.py  # 瘦仓库
adapters/driven/collectors/base.py            # 注入 HttpClient
adapters/driven/collectors/quote.py           # 注入 HttpClient + Reporter
adapters/driven/collectors/kline.py           # 注入 HttpClient + Reporter
adapters/driven/collectors/capital_flow.py    # 注入 HttpClient + Reporter
adapters/driven/collectors/research_report.py # 注入 HttpClient + Reporter
adapters/driven/collectors/social_orchestrator.py  # 注入 BrowserFactory + Reporter
adapters/driven/ai/analyzer.py               # 改为门面模式
adapters/driven/report/generator.py           # 注入改造
adapters/driving/cli/pipeline.py              # 使用 Container
core/domain/aggregates/stock_analysis.py      # Optional 字段 + extensions
```

### 3.3 删除/废弃

```
# 无删除 — 全部向后兼容
```

## 4. 实施路径

### Phase 1: 基础设施（无行为变更）

- [ ] 1.1 添加 `ProgressReporter` Protocol + `CliProgressReporter` + `NullProgressReporter` + `RecordingProgressReporter`
- [ ] 1.2 添加 `Container`（DI 容器）
- [ ] 1.3 添加 `HttpClient` Protocol + `HttpxClient`
- [ ] 1.4 添加 `BrowserFactory` Protocol + `PlaywrightBrowserFactory`

**验证**：CLI 运行 `aimoon 600519 --mock` 输出不变

### Phase 2: 采集器可测试化

- [ ] 2.1 `QuoteCollector` 改造 + 测试（fallback 链、失败降级）
- [ ] 2.2 `KlineCollector` 改造 + 测试（3级降级、数据解析）
- [ ] 2.3 `CapitalFlowCollector` 改造 + 测试（多源合并、零值检测）
- [ ] 2.4 `ResearchReportCollector` 改造 + 测试（空结果处理）
- [ ] 2.5 `SocialMediaOrchestrator` 改造 + 测试（部分失败→mock 降级）

**验证**：`pytest tests/collectors/` 全部通过

### Phase 3: 仓库拆分

- [ ] 3.1 提取 `CollectorOrchestrator` + `CollectPayload`
- [ ] 3.2 `CompositeStockAnalysisRepository` 瘦身为组合查询
- [ ] 3.3 更新 `PipelineOrchestrator` 使用新接口

**验证**：CLI 运行 `aimoon 600519 --mock` 输出不变

### Phase 4: AI Analyzer 拆分

- [ ] 4.1 提取 `PromptBuilder` + 测试
- [ ] 4.2 提取 `PostProcessor` + 测试
- [ ] 4.3 提取 `DeepSeekApiClient` + 测试
- [ ] 4.4 `DeepSeekAIAnalyzer` 改为门面模式

**验证**：`pytest tests/ai/` 全部通过

### Phase 5: 报告生成器测试

- [ ] 5.1 `_md_to_html` 测试（XSS 清洗、换行处理）
- [ ] 5.2 `_build_context` 提取 + 测试
- [ ] 5.3 `HtmlReportGenerator` 注入改造 + 测试
- [ ] 5.4 集成测试：端到端生成报告

**验证**：`pytest tests/report/` 全部通过 + 报告输出不变

### Phase 6: StockAnalysis 瘦身（可选）

- [ ] 6.1 改为 Optional 字段
- [ ] 6.2 添加 `extensions` dict
- [ ] 6.3 更新所有消费者（报告模板、AI prompt）
- [ ] 6.4 全面回归测试

**验证**：`pytest` 全部通过 + 报告输出不变

## 5. 测试策略

### 5.1 测试结构

```
tests/
├── conftest.py                      # 共享 fixtures
├── domain/
│   └── test_symbols.py              # 领域服务测试
├── collectors/
│   ├── test_quote.py                # QuoteCollector 测试
│   ├── test_kline.py                # KlineCollector 测试
│   ├── test_capital_flow.py         # CapitalFlowCollector 测试
│   ├── test_research_report.py      # ResearchReportCollector 测试
│   └── test_social_orchestrator.py  # SocialMediaOrchestrator 测试
├── ai/
│   ├── test_prompt_builder.py       # PromptBuilder 测试
│   ├── test_post_processor.py       # PostProcessor 测试
│   └── test_api_client.py           # DeepSeekApiClient 测试
├── report/
│   ├── test_md_to_html.py           # _md_to_html 测试
│   ├── test_build_context.py        # _build_context 测试
│   └── test_generator.py            # HtmlReportGenerator 测试
└── integration/
    └── test_pipeline.py             # 端到端流水线测试
```

### 5.2 测试覆盖目标

| 组件 | 覆盖场景 |
|------|----------|
| QuoteCollector | 主源成功；主源失败→fallback；全部失败→空对象 |
| KlineCollector | akshare 成功；akshare 失败→tencent；数据解析 |
| CapitalFlowCollector | 多源合并；零值检测；全部失败降级 |
| ResearchReportCollector | 正常结果；空结果 |
| SocialMediaOrchestrator | 全部成功；部分失败→mock 降级；全部失败 |
| PromptBuilder | 完整数据；部分缺失数据；空数据 |
| PostProcessor | 摘要提取；支撑位修正（过高/过低/正常） |
| DeepSeekApiClient | 正常响应；工具调用；流式响应 |
| _md_to_html | XSS 被清洗；换行转为 `<br>`；表格渲染 |
| HtmlReportGenerator | 文件写入；模板渲染；空数据不崩溃 |

### 5.3 测试工具

```python
# conftest.py — 共享 fixtures

@pytest.fixture
def fake_http() -> FakeHttpClient:
    """提供一个空的 FakeHttpClient。"""
    return FakeHttpClient()

@pytest.fixture
def null_reporter() -> NullProgressReporter:
    """提供一个静默的 ProgressReporter。"""
    return NullProgressReporter()

@pytest.fixture
def recording_reporter() -> RecordingProgressReporter:
    """提供一个记录调用的 ProgressReporter。"""
    return RecordingProgressReporter()

@pytest.fixture
def sample_stock() -> StockAnalysis:
    """提供一个最小化的 StockAnalysis 用于测试。"""
    return StockAnalysis(
        symbol="600519",
        name="贵州茅台",
        quote=StockQuote(symbol="600519", name="贵州茅台", price=1500.0, source="test"),
    )
```

## 6. 兼容性保障

| 保障项 | 方式 |
|--------|------|
| CLI 命令不变 | `main.py` 接口不变 |
| 报告 HTML 不变 | 模板不变 + 上下文结构不变 |
| .env 配置不变 | `Settings` 类不变 |
| 外部行为不变 | 每阶段 CLI 运行对比输出 |
| 旧代码兼容 | `get_collect_results()` 保留委托 |

## 7. 新增依赖

**无新依赖** — 全部用 Python 标准库 + 已有依赖。

DI 容器、Protocol、测试工具全部手写。

## 8. 风险与缓解

| 风险 | 缓解 |
|------|------|
| StockAnalysis 瘦身导致模板渲染失败 | Phase 6 可选；模板用 `getattr` 防御 |
| 采集器改造引入回归 | 每阶段 CLI 对比输出 |
| DI 容器增加复杂度 | 手写 ~50 行，无魔法 |
| 测试维护成本 | 纯函数测试极稳定；IO 测试用 Fake 实现 |

## 9. 成功标准

- [ ] 采集器测试覆盖率 > 80%
- [ ] 报告生成器测试覆盖率 > 80%
- [ ] AI 分析器各组件有独立测试
- [ ] 所有现有 CLI 命令输出不变
- [ ] 报告 HTML 输出不变
- [ ] `pytest` 全部通过
- [ ] `ruff check src/` 通过
- [ ] `mypy src/aimoon/` 通过
