# aimoon 架构重构设计：六边形精练（全量路线）

**日期**: 2026-07-09
**状态**: 已确认，实施中
**版本**: v0.4.2 → v0.5.0（架构优化，外部兼容）
**关联**: `docs/audit_2026-07-08.md` · `docs/superpowers/specs/2026-06-29-architecture-refinement-design.md`

## 1. 背景与目标

### 1.1 决策摘要

| 维度 | 决策 |
|------|------|
| 范围 | P0+P1+P2 全清（P0/P1 经实证核查大部分已落地，剩文档同步与零星死代码） |
| 野心级别 | 激进路线——完整落地 2026-06-29 架构精练设计稿 |
| 执行策略 | 分 6 阶段，每阶段完成后跑 `ruff + mypy + pytest -m "not integration" + aimoon 600519 --mock` 四件套验证 |

### 1.2 优化目标

1. **可测试性** — 采集器和 AI 分析器各组件可独立单测，摆脱对网络/真实 API 的依赖
2. **职责分离** — 每个组件单一职责，最大文件从 865 行降至 ~200 行
3. **架构纯净** — 适配器之间互不依赖，只通过 core/ 端口通信
4. **外部兼容** — CLI 命令、报告 HTML 格式、.env 配置全程不变
5. **零新依赖** — 全部用标准库 + 已有依赖

## 2. 阶段总览

| Phase | 主题 | 对应审计项 | 核心产出 |
|-------|------|-----------|---------|
| 1 | 基础设施层（零行为变更） | P2.3 前置 | DI Container、ProgressReporter、HttpClient/BrowserFactory 抽象 |
| 2 | 采集器可测试化 | P2.4 资源复用 | 6 个采集器注入改造 + 每个 collector 独立测试 |
| 3 | 仓库拆分 + 跨适配器解耦 | P2.3 | CollectorOrchestrator 提取、CompositeRepository 瘦身、logphase/mock 下沉 |
| 4 | AI Analyzer 拆分 + orchestrator.py 拆分 | P2.1 | PromptBuilder/ApiClient/PostProcessor 三件套 + pipeline/orchestrator.py 865→5 文件 + SSE 统一 |
| 5 | DRY 去重 + 魔法数字 + 资源复用 | P2.2 + P2.4 + P2.5 | tools/_common.py、industry_map.py、常量提取、浏览器/httpx 复用 |
| 6 | StockAnalysis 瘦身 + 测试补全 + 文档同步 | P3 + BUG-5 | Optional 字段 + extensions dict、补关键模块测试、文档同步 |

**依赖链**: Phase 1 → 2 → 3（采集侧）和 Phase 1 → 4（AI 侧）可并行，但 3 和 4 都依赖 1 的抽象。Phase 5 依赖 3+4 完成后才能去重。Phase 6 最后收尾。

## 3. 各阶段详细设计

### 3.1 Phase 1 — 基础设施层

4 个新文件，全在 `core/application/` 下，纯新增不改行为。

**1.1 DI Container**（`core/application/container.py`，~50 行）

手写容器，无反射无魔法。`resolve(cls)` 按类型创建单例，`override(cls, instance)` 供测试替换。`_create()` 是硬编码工厂——每种适配器类型对应一个创建分支，IDE 可追踪。替代当前 `PipelineOrchestrator.__init__` 里手动 new 一堆适配器的硬编码组装。

**1.2 ProgressReporter**（`core/application/progress.py`）

Protocol + 三个实现：`CliProgressReporter`（生产，保持现有 print 行为）、`NullProgressReporter`（测试静默）、`RecordingProgressReporter`（测试记录调用供断言）。替换采集器里散落的 `print()`，让输出可断言。

**1.3 HttpClient**（`core/application/http_client.py`）

Protocol + `HttpxClient` 包装 `httpx.AsyncClient`，返回 `HttpResponse` 值对象（frozen）。`FakeHttpClient` 供测试按 URL 模式返回预设响应。解决当前 cninfo/eastmoney 各自新建 httpx 客户端的问题。

**1.4 BrowserFactory**（`core/application/browser_factory.py`）

Protocol + `PlaywrightBrowserFactory`，内部管理 Playwright 单例 + asyncio.Lock 双重检查。替换 `social_orchestrator.py` 的模块级可变单例。`release()` 空操作保持热启动，`shutdown()` 在 CLI 退出时调用。

**验证**: `aimoon 600519 --mock` 输出与改造前完全一致（抽象就位但还没被消费）。

### 3.2 Phase 2 — 采集器可测试化

把 Phase 1 的抽象注入到 6 个采集器，让每个都能独立单测。改动模式统一：构造函数接收 `HttpClient` + `ProgressReporter`，内部 `print()` 换成 `reporter.report()`，HTTP 调用走注入的 client。

| 采集器 | 注入项 | 测试覆盖场景 |
|--------|--------|-------------|
| QuoteCollector | HttpClient + Reporter | 主源成功；xueqiu→sina→tencent 三级 fallback；全失败→空对象 |
| KlineCollector | HttpClient + Reporter | akshare 成功；akshare 失败→tencent fqkline；数据解析（腾讯 volume=手） |
| CapitalFlowCollector | HttpClient + Reporter | pysnowball+akshare+eastmoney 多源合并；零值检测；全失败降级 |
| ResearchReportCollector | HttpClient + Reporter | 正常结果；空结果 |
| SocialMediaOrchestrator | BrowserFactory + Reporter | 全部成功；部分失败→mock 降级；全失败 |
| AkshareFinancialAdapter | Reporter | 24h 缓存命中；`_fetch_triple` 去重；空 DataFrame |

**关键约束**:
- 构造函数加默认值 `http_client: HttpClient | None = None`，`None` 时内部懒创建——向后兼容
- broad-except 容错契约不变
- lazy import 不动（akshare/playwright 仍在函数内导入）

**测试结构**: `tests/collectors/` 下每个采集器一个文件，用 `FakeHttpClient` + `NullProgressReporter`，零网络依赖。

### 3.3 Phase 3 — 仓库拆分 + 跨适配器解耦

**3.1 仓库拆分**

`CompositeStockAnalysisRepository`（300 行）拆成：
- `CollectorOrchestrator`（`adapters/driven/collectors/orchestrator.py`，新文件）——管并发、错误处理、结果聚合。产出 `CollectPayload`（frozen dataclass）。持有各采集器引用 + ProgressReporter。
- `CompositeStockAnalysisRepository`（瘦身后 ~50 行）——只做组合查询，委托给 orchestrator。保留 `get_collect_results()` 委托，旧接口不破。

应用层 `collect_and_analyze()` 改为单次 `payload = await orchestrator.orchestrate(symbol, name)`，告别当前"先 collect_all 再 get_collect_results"的二次调用时序依赖。

**3.2 跨适配器解耦**（审计 P2.3）

| 越界依赖 | 现状 | 修复 |
|---------|------|------|
| `composite_repo.py:16` → `ai.pipeline.timing` | collectors 依赖 ai 层 `logphase` | `logphase` 移到 `adapters/driven/common/timing.py` |
| `analyzer.py:186` → `collectors.mock` | ai 层依赖 collectors 的 `mock_analysis_report` | 移到 `adapters/driven/common/mock.py` |

移动后 collectors/ 不再 import ai/，ai/ 不再 import collectors/——适配器之间只通过 core/ 端口通信。

**验证**: `grep -rn "from aimoon.adapters.driven.ai" src/aimoon/adapters/driven/collectors/` 应 0 结果；反向同理。

### 3.4 Phase 4 — AI Analyzer 拆分 + orchestrator.py 拆分 + SSE 统一

**4.1 AI Analyzer 三件套**（`adapters/driven/ai/`）

| 新文件 | 职责 | 行数 | 测试性 |
|--------|------|------|--------|
| `prompt_builder.py` | StockAnalysis → PromptContext → user message，纯函数 | ~120 | 零 IO，直接单测 |
| `api_client.py` | DeepSeek HTTP/SSE 通信 + 工具调用循环，注入 httpx client | ~150 | 注入 mock transport |
| `post_processor.py` | 摘要提取 + 支撑位/阻力位修正，纯函数 | ~80 | 零 IO，直接单测 |
| `analyzer.py`（瘦身后） | 门面：组装三件套 + 流式输出打印 | ~80 | 集成测试 |

`PromptContext` 是 frozen dataclass，把 StockAnalysis 的 13+ 字段扁平化成 prompt 构建所需的字典。

**4.2 orchestrator.py 拆分**（`adapters/driven/ai/pipeline/`，审计 P2.1）

| 新文件 | 从原文件提取 | 行数 |
|--------|------------|------|
| `llm_client.py` | `_stream_llm_content` + SSE 读取 + reasoning_effort 逻辑 | ~110 |
| `context_renderer.py` | 标的快照 Markdown 渲染 | ~60 |
| `tool_summaries.py` | 6 个工具摘要格式化函数 | ~250 |
| `utils.py` | partial/run_safe/_run_peer_compare/JSON 解析 | ~80 |
| `orchestrator.py`（精简后） | 纯编排：3 个阶段方法 + run() | ~200 |

**4.3 SSE 统一**

`analyzer.py` 和 `orchestrator.py` 各有一份 SSE 读取逻辑，本 Phase 内统一提取到 `ai/_sse.py`（~40 行），两边都改为从 `_sse.py` 导入。

**关键约束**:
- `_phase_compile` 的 `reasoning_effort="medium"` 不动
- `_phase_self_check` 独立质量门保留
- 每拆一个文件跑一次全量测试，绿了再拆下一个

### 3.5 Phase 5 — DRY 去重 + 魔法数字 + 资源复用

**5.1 DRY 去重**（SSE 已在 Phase 4 解决，剩 5 处）

| 重复项 | 位置1 | 位置2 | 提取到 |
|--------|-------|-------|--------|
| `_first_year_ocf` | valuation.py:83 | fcf_dividend.py:87 | tools/_common.py |
| `_capex` | valuation.py:99 | fcf_dividend.py:103 | tools/_common.py |
| `INDUSTRIAL_CAPEX_OCF_RATIO` | valuation.py:15 | fcf_dividend.py:25 | tools/_common.py |
| `_hist_pe_anchor` | risk_quant.py:222 | scenario_prob.py:160 | tools/_common.py |
| `_detect_industry` | analyzer.py:42（Phase 4 后在 prompt_builder） | peer_compare.py:137 | common/industry_map.py |

**5.2 魔法数字提取**（审计 P2.5）

- orchestrator.py（精简后）6 个超时值 → 模块级 `TIMEOUT_*` 常量
- risk_quant.py 6 个阈值 → 模块级常量
- valuation.py 2 个比率 → 随 5.1 进 tools/_common.py

**5.3 资源复用**（审计 P2.4 剩余项）

- 浏览器：移除 `pipeline.py` 每次分析的 `close_shared_browser()`，改为 CLI 退出时调 `BrowserFactory.shutdown()`。批量分析每只省 ~1s。
- httpx 客户端：cninfo/eastmoney 各自新建的 httpx 客户端，改为复用共享 `httpx.AsyncClient`。省 0.3-0.5s/次。

### 3.6 Phase 6 — StockAnalysis 瘦身 + 测试补全 + 文档同步

**6.1 StockAnalysis 瘦身**

- 核心字段（symbol/name/market/collected_at）保持必填
- 数据字段（quote/financial/kline/capital_flow/research/social_posts）改为 Optional + 默认 None
- 新增 `extensions: dict[str, BaseModel]` 容纳未来维度，配 `get_extension(key, cls)` 安全取值
- social_posts 从 list 改 tuple（不可变，符合值对象语义）

兼容保障：报告模板用 `getattr(info, "quote", None)` 防御；PromptBuilder 已通过 PromptContext 扁平化。

**6.2 测试补全**（审计 P3.1，优先 5 个模块）

| 优先级 | 模块 | 当前 | 目标 |
|--------|------|------|------|
| 1 | integrity_checker.py（210 行） | 零测试 | 数据质量守门人完整测试 |
| 2 | capital_flow.py（251 行） | 仅测死代码 | fallback 链 + 多源合并 |
| 3 | scenario_prob.py/sentiment.py/fcf_dividend.py | 仅降级测试 | 正常路径 |
| 4 | stock_analysis_service.py（193 行） | 部分未测 | 主入口全覆盖 |
| 5 | COMPILE 输出完整性 | 未验证 | ANALYSIS 数据整合校验 |

**6.3 文档同步**（审计 BUG-5，7 处）

- CLAUDE.md 版本号 0.4.0→0.4.2、Tencent 单位修正
- ARCHITECTURE.md 删已不存在的 xueqiu.py/pysnowball_adapter.py/annual_report.py 引用
- README/AGENTS.md 补 --fast 参数
- 三文件统一「scoring.py 实际不存在」口径
- 剩余零星死代码 grep 确认 0 引用后删

## 4. 文件变更清单

### 4.1 新增文件

```
core/application/container.py              # DI 容器
core/application/progress.py               # ProgressReporter Protocol + 实现
core/application/http_client.py            # HttpClient Protocol + HttpxClient
core/application/browser_factory.py        # BrowserFactory Protocol + 实现
adapters/driven/collectors/orchestrator.py # CollectorOrchestrator
adapters/driven/ai/prompt_builder.py       # PromptBuilder
adapters/driven/ai/api_client.py           # DeepSeekApiClient
adapters/driven/ai/post_processor.py       # PostProcessor
adapters/driven/ai/_sse.py                 # SSE 读取统一
adapters/driven/ai/pipeline/llm_client.py  # LLM HTTP/SSE 通信
adapters/driven/ai/pipeline/context_renderer.py  # 标的快照渲染
adapters/driven/ai/pipeline/tool_summaries.py    # 工具摘要格式化
adapters/driven/ai/pipeline/utils.py       # partial/run_safe/JSON 解析
adapters/driven/common/timing.py           # logphase（从 ai.pipeline.timing 移入）
adapters/driven/common/mock.py             # mock_analysis_report（从 collectors.mock 移入）
adapters/driven/common/industry_map.py     # _detect_industry 统一
adapters/driven/ai/tools/_common.py        # 重复函数提取
tests/collectors/                          # 采集器测试目录
tests/ai/                                  # AI 组件测试目录
```

### 4.2 修改文件

```
adapters/driven/collectors/composite_repo.py     # 瘦身为委托
adapters/driven/collectors/base.py               # 注入 HttpClient
adapters/driven/collectors/quote.py              # 注入 HttpClient + Reporter
adapters/driven/collectors/kline.py              # 注入 HttpClient + Reporter
adapters/driven/collectors/capital_flow.py       # 注入 HttpClient + Reporter
adapters/driven/collectors/research_report.py    # 注入 HttpClient + Reporter
adapters/driven/collectors/social_orchestrator.py # 注入 BrowserFactory + Reporter
adapters/driven/collectors/cninfo.py             # 复用共享 httpx
adapters/driven/collectors/eastmoney_playwright.py # 复用共享 httpx
adapters/driven/financial/akshare_adapter.py     # 注入 Reporter + 删死代码
adapters/driven/ai/analyzer.py                   # 改为门面模式
adapters/driven/ai/pipeline/orchestrator.py      # 精简为纯编排
adapters/driven/report/generator.py              # 注入改造
adapters/driving/cli/pipeline.py                 # 使用 Container + 浏览器复用
adapters/driving/cli/main.py                     # CLI 退出时 shutdown
core/application/services/stock_analysis_service.py # 单次 orchestrate() 调用
core/domain/aggregates/stock_analysis.py         # Optional 字段 + extensions
CLAUDE.md / ARCHITECTURE.md / README.md / AGENTS.md  # 文档同步
```

## 5. 验证策略

每个 Phase 完成后必须通过四件套：

```bash
uv run ruff check src/                         # lint
uv run mypy src/aimoon/                        # 类型检查
uv run pytest -m "not integration" -q          # 全量测试（离线）
aimoon 600519 --mock                           # 端到端冒烟
```

关键回归测试对应：
- Phase 2: `pytest tests/collectors/`
- Phase 4: `pytest tests/ai/` + `test_pipeline_phases.py`（3-phase 枚举 + 4 variant JSON + 2 integration）
- Phase 5: grep 确认重复函数名只在共享模块定义
- Phase 6: `grep -rn "xueqiu\.py\|pysnowball_adapter\|annual_report\.py" docs/` 应 0 结果

## 6. 风险与缓解

| 风险 | Phase | 概率 | 处置 |
|------|-------|------|------|
| DI 容器过度设计 | 1 | 低 | 手写 ~50 行，无魔法；IDE 可追踪 |
| 采集器注入改造引入回归 | 2 | 中 | 默认值保兼容；靠测试覆盖兜住 |
| 仓库拆分破坏 get_collect_results | 3 | 中低 | 保留委托；logphase/mock 是纯函数移动 |
| AI 核心逻辑拆分引入回归 | 4 | 高 | 每拆一个文件跑一次全量测试 |
| StockAnalysis Optional 改造波及消费者 | 6 | 中 | 模板 getattr 防御 + 全量测试 |

## 7. 成功标准

- [ ] 最大文件从 865 行降至 ~200 行
- [ ] 适配器之间零跨层 import（collectors↔ai）
- [ ] 采集器测试覆盖率 > 80%
- [ ] AI 分析器各组件有独立测试
- [ ] 所有现有 CLI 命令输出不变
- [ ] 报告 HTML 输出不变
- [ ] `ruff check src/` 通过
- [ ] `mypy src/aimoon/` 通过
- [ ] `pytest -m "not integration"` 全绿
- [ ] 6 处 DRY 违规消除
- [ ] 文档引用 0 死链
