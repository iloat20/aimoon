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
  - `services/` — Pure domain services: symbol resolution（注：`scoring.py` 评分模块实际并不存在）
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

Flow: quote(xueqiu→akshare) → financial(pysnowball) → K-line(akshare) → fund_flow(pysnowball→akshare→eastmoney HTTP) → research(akshare) → social(guba→cninfo→toutiao→wechat) → cninfo reports → validation → AI analyze → HTML report.

Playwright collectors (guba, toutiao, wechat) spin up real browsers — first run install via `uv run playwright install chromium`.

## Key quirks

- **pysnowball for financials** — requires `XUEQIU_TOKEN`, silently returns empty if missing
- **K-line 3-tier** — akshare `stock_zh_a_hist` (qfq) → `stock_zh_a_daily` → Tencent `fqkline`. Tencent volume = 手 × 100
- **East Money `push2*.eastmoney.com`** — all subdomains connection reset (HTTP 000), curl CLI same
- **Xueqiu WAF** — `xueqiu.com` main domain blocked by Alibaba WAF; `stock.xueqiu.com` subdomain for quotes/financials only
- **Collectors never fail pipeline** — each has mock fallback; exceptions silently caught
- **Social data cleaning** — `adapters/driven/ai/data_cleaner.py`: extracts dates/numbers/keywords, strips HTML/noise, scores lines by relevance before feeding to model
- **Streaming analysis** — `analyzer.py:analyze_stock` uses `stream=True`, prints section headers (`##`) as they arrive, accumulates full response for HTML report
- **Tool calling for web search** — DeepSeek API `tools` + `tool_choice="auto"` triggers `web_search_tool.py` which scrapes Bing (primary) → DuckDuckGo (fallback); max 5 rounds before forced final response
- **Support/resistance sanity** — `adapters/driven/ai/analyzer.py`: if support ≥ price or resistance ≤ price, override to price×0.92/1.08
- **Capital flow override** — 文档曾描述 `core/domain/services/scoring.py` 中「if 0 < turnover < 0.1%, 强制"交投清淡, 观望"」,该评分模块实际不存在(详见 CLAUDE.md「Scoring」段)
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
- **Intentional broad exceptions**: All collectors use `except Exception: pass` — single-source failures must never abort the pipeline.
- **East Money guba market code**: SZ stocks use `"0"` not `"2"` in guba URLs. Playwright and HTML fallback must match.
- **Theme toggle script placement**: Must precede external CDN scripts (chart.js, html2canvas, jspdf) for click handler to attach in time.
- **Debug scripts in project root**: `debug_toutiao*.py`, `test_fund_flow*.py` are one-shot artifacts, not part of the package.
- **Tests exist but minimal**: `tests/test_pipeline.py`, `tests/test_orchestrator_wiring.py`. No CI, no pre-commit.
- **Testing domain logic**: All domain services (`core/domain/services/`) are pure functions — test directly without any mocks or IO.
