# aimoon — AI A-Share Stock Analysis

## Quick start

```bash
uv sync                                    # install deps
pip install -e .                           # install CLI globally
uv run playwright install chromium         # browser for Playwright collectors
cp .env.example .env                       # then edit with API keys
aimoon 600519                              # real analysis
aimoon 600519 --mock                       # no API keys needed
```

## Commands

| Command | What |
|---|---|
| `aimoon <code>` | Full pipeline: collect → validate → AI analyze → HTML report |
| `aimoon <code> --mock` | Mock data, no API keys required |
| `aimoon <code> --test` | Real data collection, skip AI analysis (no DeepSeek) |
| `aimoon test <code>` | Same as `--test` (convenience) |
| `aimoon <code> -o ./reports` | Custom output directory |

## Architecture

**Hexagonal Architecture (Ports & Adapters)** with DDD principles.

```
                    ┌──────────────────────────────────────────┐
                    │           Adapters (Driving)            │
                    │  CLI entry point  ·  PipelineOrchestrator│
                    └─────────────────────┬────────────────────┘
                                          │
                    ┌─────────────────────▼────────────────────┐
                    │        Application Layer (Core)          │
                    │  services/  ·  ports/ (output ports)     │
                    │  — orchestration only, no business logic │
                    └─────────────────────┬────────────────────┘
                                          │
                    ┌─────────────────────▼────────────────────┐
                    │          Domain Layer (Core)             │
                    │  aggregates  ·  entities  ·  value objs  │
                    │  domain services  ·  repository ports    │
                    │  — pure business logic, no IO            │
                    └─────────────────────┬────────────────────┘
                                          │
                    ┌─────────────────────▼────────────────────┐
                    │          Adapters (Driven)               │
                    │  collectors  ·  AI analyzer  ·  report   │
                    │  validation  ·  config  ·  financial     │
                    └──────────────────────────────────────────┘
```

### Layer structure

- **`core/domain/`** — Domain model (no external deps except Pydantic)
  - `aggregates/stock_analysis.py` — StockAnalysis aggregate root
  - `entities/` — Entities with identity: quote, financial, kline, capital_flow, social, research
  - `value_objects/` — Immutable value objects: KlineBar, DimensionScore, AnalysisReport, CollectResult, FinancialReport
  - `services/` — Pure domain services: symbol resolution（注：旧文档所称 `scoring.py` 11 因子评分模块实际不存在；逐维评分在 `adapters/driven/validation/integrity_checker.py`）
  - `repositories/` — Repository interface (input port for data access)

- **`core/application/`** — Application layer (orchestration only)
  - `services/stock_analysis_service.py` — `collect_and_analyze()` function, main use case
  - `ports/` — Output port interfaces: AIAnalyzer, DataValidator, ReportGenerator

- **`adapters/driving/`** — Driving adapters (input side)
  - `cli/main.py` — CLI entry point
  - `cli/pipeline.py` — PipelineOrchestrator, assembles all adapters

- **`adapters/driven/`** — Driven adapters (output side)
  - `collectors/` — Data collection adapters (composite repo pattern)
  - `ai/` — DeepSeek AI analysis adapter
  - `report/` — HTML report generator (Jinja2)
  - `validation/` — Data integrity validation
  - `financial/` — Financial report adapters
  - `config/` — Settings/config adapter

### Key design decisions

- **Function-based application services** — no class-based UseCases; simple functions with explicit dependency injection
- **Composite Repository** — `CompositeStockAnalysisRepository` combines many collectors behind one `StockAnalysisRepository` port
- **Unified Pydantic models** — single model layer, no dual dataclass/Pydantic system
- **Dependency rule**: `core/` never imports from `adapters/`; all dependencies point inward

## Pipeline

`adapters/driving/cli/pipeline.py:PipelineOrchestrator` assembles adapters → calls `core/application/services/stock_analysis_service.py:collect_and_analyze()`.

Flow: quote(xueqiu→sina→tencent 三级兜底) → financial(akshare + 新浪兜底, 含巨潮年报 PDF 附注解析) → K-line(akshare) → fund_flow(pysnowball→akshare→eastmoney HTTP) → research(akshare) → social(guba→cninfo→toutiao→wechat) → cninfo reports → validation → AI analyze → HTML report.

Playwright collectors (guba, toutiao, wechat) spin up real browsers — first run install via `uv run playwright install chromium`.

## Key quirks

- **财务三表已迁 akshare** — `AkshareFinancialAdapter`（`cli/pipeline.py:52` 注入），不再用 pysnowball 拉财务；东财 F10 WAF 期间自动回退新浪 `stock_financial_report_sina`。pysnowball 现仅作**资金流主源**（见下方 Capital flow）。
- **K-line 3-tier** — akshare `stock_zh_a_hist` (qfq) → `stock_zh_a_daily` → Tencent `fqkline`. Tencent volume = 手 × 100
- **East Money `push2*.eastmoney.com`** — all subdomains connection reset (HTTP 000), curl CLI same
- **Xueqiu WAF** — `xueqiu.com` main domain blocked by Alibaba WAF; `stock.xueqiu.com` subdomain for quotes only
- **Collectors never fail pipeline** — each has mock fallback; exceptions silently caught
- **Social data cleaning** — `adapters/driven/ai/post_processor.py`: extracts dates/numbers/keywords, strips HTML/noise before feeding to model（注：旧文档误引不存在的 `ai/data_cleaner.py`，实际清洗逻辑在此文件）
- **Streaming analysis** — v2 流水线 `ai/pipeline/orchestrator.py` 走 `stream=True` 流式直出；`DeepSeekAIAnalyzer.analyze(use_pipeline_v2=True)` 走此路径（受支持路径）。
- **DEPRECATED: legacy 单发路径** — `analyzer.py:_legacy_analyze`（`use_pipeline_v2=False`，即 `analyze()` 默认）**已弃用**，计划移除（架构审查 #6, 2026-07-19）。其 `analysis:*` 缓存读是 v2 写入的跨路径（写多读少），请勿在 legacy 分支新增行为；移除前需同步改 `test_ai.py` / `test_integration_pipeline.py` 的路由断言。
- **Tool calling for web search** — DeepSeek API `tools` + `tool_choice="auto"` triggers `web_search_tool.py` which scrapes Bing (primary) → DuckDuckGo (fallback); max 5 rounds before forced final response
- **Support/resistance sanity** — `adapters/driven/ai/analyzer.py`: if support ≥ price or resistance ≤ price, override to price×0.92/1.08
- **评分模块位置** — 旧文档称 `core/domain/services/scoring.py`（11 因子加权）；该评分模块实际不存在。真实的逐维置信评分在 `adapters/driven/validation/integrity_checker.py`（各维度 1-5 评分 + 确定性校验）。
- **Report**: `output/<symbol>_<timestamp>.html`; inline CSS Jinja2 template; all dimensions scored 1-5
- **No 小红书/抖音 collector** — explicitly removed by user

Stock code → market: `6xxxx` → SH, `0/3xxxx` → SZ, `4/8xxxx` → BJ.

## Config

Pydantic-settings loads from `.env` (in .gitignore). Key vars:

- `DEEPSEEK_API_KEY` — required for real AI analysis
- `XUEQIU_COOKIE` + `XUEQIU_TOKEN` — for quotes with PE + financial data
- `MOCK_MODE=true` — env var alternative to `--mock`

## Dev

```bash
uv sync --group dev          # pytest + ruff + bandit + pylint
uv run ruff check src/       # line-length 100, select E/F/I/N/W/UP
uv run mypy src/aimoon/      # ignore_missing_imports, warn_unused_ignores
```

- **Lint before commit**: `ruff check src/` then `mypy src/aimoon/` — both must pass.
- **Python 3.12+ syntax required**: Use `class Foo[T](ABC):` not `class Foo(ABC, Generic[T]):` (ruff UP046).
- **Subclass fetch signature**: All `BaseDataCollector.fetch()` overrides must include `**kwargs: Any` to match supertype.
- **Intentional lazy imports**: Heavy deps (akshare, playwright, pysnowball) imported inside functions to avoid startup cost. Do not hoist.
- **Collector exception handling is mixed, not uniform**: Most collectors guard with broad `except Exception`; a few (`QuoteCollector`, and parts of `cninfo`/`research_report`) use narrow exception tuples. The primary isolation is the parallel `asyncio.gather(return_exceptions=True)` in `CollectorOrchestrator`; the quote path runs outside that gather and is additionally guarded in `_fetch_quote` so a quote failure downgrades to a failed result instead of aborting the pipeline.
- **East Money guba market code**: SZ stocks use `"0"` not `"2"` in guba URLs. Playwright and HTML fallback must match.
- **Theme toggle script placement**: Must precede external CDN scripts (chart.js, html2canvas, jspdf) for click handler to attach in time.
- **Debug scripts in project root**: only `verify_financials_000651.py` remains (one-shot verification artifact, not part of the package). The older `debug_toutiao*.py` / `test_fund_flow*.py` no longer exist.
- **Adapter placement**: concrete IO-bearing implementations (httpx client, Playwright browser factory, progress reporters) live in `adapters/driven/common/`; `core/application/` holds only ports (Protocols) and the DI container. Never place concrete adapter classes in `core/`.
- **Tests exist but minimal**: `tests/test_pipeline.py`, `tests/test_orchestrator_wiring.py`. No CI, no pre-commit.
- **Testing domain logic**: All domain services (`core/domain/services/`) are pure functions — test directly without any mocks or IO.
