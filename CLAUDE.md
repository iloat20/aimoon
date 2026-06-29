# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**aimoon** is an AI-powered A-share (Chinese domestic stock) analysis tool. Input a stock code → automated pipeline: collect data → validate → AI analyze → generate HTML report. Python 3.12+, MIT license, version 0.4.0.

## Commands

```bash
# Install
uv sync                                    # install dependencies
pip install -e .                           # install CLI globally
uv run playwright install chromium         # browser for Playwright collectors

# Run analysis
aimoon 600519                              # real data + AI analysis
aimoon 600519 --mock                       # mock data, no API keys
aimoon 600519 --test                       # real data, skip AI analysis
aimoon test 600519                          # same as --test
aimoon 000001 -o ./reports                 # custom output directory

# Lint & type check (both must pass before commit)
uv run ruff check src/                     # line-length 100, rules: E/F/I/N/W/UP
uv run mypy src/aimoon/                    # ignore_missing_imports, warn_unused_ignores

# Tests
uv run pytest                              # run all tests
uv run pytest tests/test_scoring.py         # single test file
uv run pytest tests/test_scoring.py::TestCapitalFlowScore::test_neutral_flow  # single test

# Dev dependencies (pytest, ruff, bandit, pylint)
uv sync --group dev
```

## Architecture

### Hexagonal Architecture (Ports & Adapters) + DDD

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

### Layer Responsibilities

- **`core/domain/`** — Pure business logic, no external deps (except Pydantic)
  - `aggregates/stock_analysis.py` — StockAnalysis aggregate root
  - `entities/` — Entities with identity (quote, financial, kline, capital_flow, social, research)
  - `value_objects/` — Immutable value objects (KlineBar, DimensionScore, AnalysisReport, CollectResult, FinancialReport)
  - `services/scoring.py` — 11-factor scoring model (pure functions)
  - `services/symbols.py` — Stock code → market resolution
  - `repositories/stock_analysis_repo.py` — Repository interface (port)

- **`core/application/`** — Orchestration only
  - `services/stock_analysis_service.py` — `collect_and_analyze()` main use case function
  - `ports/` — Output port interfaces: AIAnalyzer, DataValidator, ReportGenerator

- **`adapters/driving/`** — Driving adapters (input side)
  - `cli/main.py` — CLI entry point
  - `cli/pipeline.py` — PipelineOrchestrator assembles adapters → calls application service

- **`adapters/driven/`** — Driven adapters (output side)
  - `collectors/` — Data collection adapters with CompositeRepository pattern
  - `ai/` — DeepSeek AI analyzer (tool calling + streaming)
  - `report/` — Jinja2 HTML report generator
  - `validation/` — Data integrity validator
  - `financial/` — Financial report adapters
  - `config/` — Pydantic-settings adapter
  - `common/` — Shared utilities (browser, cache, parsers, retry)

### Key Design Decisions

- **Function-based application services** — No class-based UseCases; pure functions with explicit dependency injection
- **Composite Repository** — `CompositeStockAnalysisRepository` combines multiple collectors behind one `StockAnalysisRepository` port
- **Unified Pydantic models** — Single model layer, no dual dataclass/Pydantic system
- **Dependency rule**: `core/` never imports from `adapters/`; all dependencies point inward

### Pipeline Flow

`adapters/driving/cli/pipeline.py:PipelineOrchestrator` → `core/application/services/stock_analysis_service.py:collect_and_analyze()`:

1. **Collect** → quote (xueqiu→sina→tencent 3-tier fallback) → financial (akshare) → K-line (akshare) → fund_flow (pysnowball+akshare+eastmoney) → research (akshare) → social (guba→cninfo→toutiao→wechat)
2. **Validate** → format check + data confidence assessment
3. **AI analyze** → DeepSeek v4-flash with deep thinking, tool calling (web search), streaming output
4. **Report** → Jinja2 + Chart.js HTML, light/dark theme

### Key Patterns

- **Two collector base classes** in `adapters/driven/collectors/base.py`:
  - `BaseCollector` — social media collectors return `CollectResult` (posts list)
  - `DataCollector[T]` — data collectors return typed models directly (quote, K-line, etc.)
  - All `fetch()` overrides **must** include `**kwargs: object` to match supertype

- **Lazy imports** — heavy deps (akshare, playwright, pysnowball) are imported inside functions, not at module level. Do not hoist.

- **Broad exception tolerance** — all collectors use `except Exception: pass`. Single-source failures must never abort the pipeline.

- **Settings** via Pydantic-settings from `.env` — `adapters/driven/config/settings.py:Settings`. Singleton via `get_settings()`. Test injection via `inject_settings()`.

- **Scoring** — 11-factor model (1-5 scale), 3 dimensions: fundamental 50% + capital flow 25% + news 25%.

### Data Sources & Quirks

- **pysnowball** for financials — requires `XUEQIU_TOKEN`, silently returns empty if missing
- **K-line 3-tier** — akshare `stock_zh_a_hist` (qfq) → `stock_zh_a_daily` → Tencent `fqkline`. Tencent volume unit = 手 × 100
- **East Money push2*.eastmoney.com** — all subdomains connection reset (HTTP 000)
- **Xueqiu WAF** — main domain blocked; only `stock.xueqiu.com` subdomain works
- **No 小红书/抖音 collectors** — explicitly removed by user
- **East Money guba market code** — SZ stocks use `"0"` not `"2"` in URLs

### Stock Code → Market Mapping

`6xxxx` → SH (Shanghai), `0/3xxxx` → SZ (Shenzhen), `4/8xxxx` → BJ (Beijing)

### Output

`output/<symbol>_<name>_<timestamp>.html` — pure static HTML, offline-viewable. Each dimension scored 1-5. Support/resistance sanity: if support ≥ price or resistance ≤ price, override to price×0.92/1.08.

## Critical Rules

- **Python 3.12+ syntax required**: Use `class Foo[T](ABC):` not `class Foo(ABC, Generic[T]):` (ruff UP046)
- **Theme toggle script placement**: Must precede external CDN scripts (chart.js, html2canvas, jspdf)
- **Debug scripts in root** (`extract_*.py`, `test_fund_flow*.py`, `send_to_ai.py`) are one-shot artifacts, not part of the package
- **No CI, no pre-commit** — tests exist but are minimal
- **Lint before commit**: `ruff check src/` then `mypy src/aimoon/` — both must pass
- **Testing domain logic**: All domain services (`core/domain/services/`) are pure functions — test directly without mocks or IO
