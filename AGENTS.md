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

## Pipeline

`pipeline.py:PipelineOrchestrator` — sequential execution. Flow: quote(xueqiu→akshare) → financial(pysnowball) → K-line(akshare) → fund_flow(pysnowball→akshare→eastmoney HTTP) → research(akshare) → social(guba→cninfo→toutiao→wechat) → cninfo reports → validation → AI analyze → HTML report.

Playwright collectors (guba, toutiao, wechat) spin up real browsers — first run install via `uv run playwright install chromium`.

## Key quirks

- **pysnowball for financials** — requires `XUEQIU_TOKEN`, silently returns empty if missing
- **K-line 3-tier** — akshare `stock_zh_a_hist` (qfq) → `stock_zh_a_daily` → Tencent `fqkline`. Tencent volume = 手 × 100
- **East Money `push2*.eastmoney.com`** — all subdomains connection reset (HTTP 000), curl CLI same
- **Xueqiu WAF** — `xueqiu.com` main domain blocked by Alibaba WAF; `stock.xueqiu.com` subdomain for quotes/financials only
- **Collectors never fail pipeline** — each has mock fallback; exceptions silently caught
- **Support/resistance sanity** — `ai/analyzer.py`: if support ≥ price or resistance ≤ price, override to price×0.92/1.08
- **Capital flow override** — `ai/analyzer.py`: if 0 < turnover < 0.1%, force "交投清淡, 观望"
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
- **Tests exist but minimal**: `tests/test_pipeline.py`, `tests/test_scoring.py`. No CI, no pre-commit.
