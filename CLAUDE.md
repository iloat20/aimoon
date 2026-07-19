# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**aimoon** is an AI-powered A-share (Chinese domestic stock) analysis tool. Input a stock code → automated pipeline: collect data → validate → AI analyze → generate HTML report. Python 3.12+, MIT license, version 0.4.2.

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
uv run pytest tests/test_pipeline_phases.py   # v2 管线 tool 链路 + 缓存命中回归

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
  - `services/symbols.py` — Stock code → market resolution（注: 旧文档所称 `scoring.py` 11 因子评分模型并不存在；逐维置信评分在 `adapters/driven/validation/integrity_checker.py`）
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

- **Scoring（逐维置信评分）** — 各维度 1-5 评分 + 数据确定性校验，实现在 `adapters/driven/validation/integrity_checker.py`（注: 旧文档所称 11 因子加权模型并不存在对应模块）。

### Data Sources & Quirks

- **财务三表已迁 akshare** — `AkshareFinancialAdapter`（`cli/pipeline.py:52` 注入），东财 F10 WAF 时自动回退新浪。pysnowball 现仅作**资金流主源**（需 `XUEQIU_TOKEN`，缺失则静默降级到 akshare 兜底）。
- **K-line 3-tier** — akshare `stock_zh_a_hist` (qfq) → `stock_zh_a_daily` → Tencent `fqkline`. Tencent volume unit = 手(腾讯接口直接返回手,无需 ×100)
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
