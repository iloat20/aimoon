# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A股量化筛选与交易建议系统 — 纯 ML 排名 + XGBoost/LightGBM 集成 + Alpha Zoo 452 因子 + 交易策略引擎。

A-share quantitative screening and trading recommendation system featuring ML-based ranking, ensemble learning (XGBoost + LightGBM), Alpha Zoo 452-factor library, and trading strategy engine.

## Development Commands

### Installation & Running

项目使用 **uv** 管理 Python 环境和依赖（无需系统级 Python 安装，uv 自动下载所需 Python 版本）。

```bash
# 首次设置：安装 uv（如果未安装）
pip install uv

# 创建虚拟环境并安装所有依赖（uv 自动下载 Python 3.13）
uv venv --python 3.13 .venv
uv pip install -e .

# 运行
aimoon                              # Run screening (default)
aimoon --demo                       # Demo mode (uses watchlist codes, no ML training)
aimoon train-model                  # Train ML models
aimoon train-model --early-stop     # With early stopping + overfit auto-recovery
aimoon train-model --optuna          # With Optuna hyperparameter search
aimoon train-model --smart-incremental  # With A/B dual model + EWC regularization
aimoon backtest                     # Run backtest
aimoon backtest --walk-forward      # Walk-forward validation
```

### Code Quality Checks

```bash
ruff check src/aimoon               # Linting (E, F, I, N, W, UP rules)
black --check src/aimoon            # Formatting check (line-length=100)
mypy src/aimoon --ignore-missing-imports  # Type checking
bandit -r src/aimoon -ll -ii        # Security scan
```

### Auto-Fix

```bash
ruff check src/aimoon --fix         # Fix import sorting, unused imports
black src/aimoon --target-version py313  # Format code
```

## Architecture

### Core Pipeline

```
CLI (cli.py)
  → Config (config.py: frozen dataclass, CLI > YAML > defaults)
    → Data Layer (data/: filters.py, history.py [mootdx→Tencent→AKShare], spot.py)
      → ML Ensemble (ml/: trainer.py, lgbm_trainer.py, ensemble.py)
        → Alpha Zoo Factors (factors/: 452 factors from gtja191, alpha101, qlib158, academic)
          → Screener (screener.py: screen_universe)
            → Output (output.py: CSV + Markdown reports)
```

### Key Modules

**Entry Point & Config**
- `cli.py` — CLI entry, argument parsing, command routing
- `config.py` — `Config` frozen dataclass, no global singleton, explicit parameter passing

**Data Layer**
- `data/filters.py` — Institutional holdings pool filtering (northbound ≥1亿, fund ≥5%, ROE >10%, PE <26, dividend >1.5%)
- `data/history.py` — Historical K-line data (mootdx → Tencent → AKShare 三级兜底)
- `data/spot.py` — Real-time market data (TTL 300s)
- `data/validator.py` — Data quality checks and fixing

**ML Pipeline** (`ml/`)
- `trainer.py` / `lgbm_trainer.py` — XGBoost + LightGBM training with Purged TimeSeriesSplit (3-fold)
- `training_loop.py` — Three training modes:
  - `run_cv_training()` — standard CV with warm-start (legacy)
  - `train_xgboost_with_early_stopping()` — consecutive-fold early stopping + overfit auto-recovery
  - `run_optuna_search()` — Bayesian hyperparameter optimization (6-fold IC mean - 0.5×std penalty)
- `ensemble.py` — Ensemble predictor with adaptive IC-weighted averaging
- `incremental_trainer.py` — SmartIncrementalLearner: A/B dual model + EWC regularization + adaptive weights
- `covariance_estimator.py` — Robust factor covariance: Ledoit-Wolf shrinkage + Marchenko-Pastur denoising + Sharpe
- `feature_pipeline.py` — Feature extraction (industry/size neutralization + factor caching)
- `alpha360.py` — Alpha360 time-series features (60-day OHLCV flattened to 360 features)
- `icir_weighter.py` — ICIR dynamic factor weighting (7-day cache, forward-looking labels)
- `factor_decay.py` — CUSUM-based factor decay detection (7-day cache)
- `purged_tscv.py` — Purged TimeSeriesSplit to prevent look-ahead bias
- `optimized_config.py` — ML hyperparameters (max_depth=2, n_estimators=500, lr=0.005, strong regularization)
- `_training_commons.py` — Shared utilities: Fisher diagonal, EWC penalty, overfit detection
- `hyperopt.py` — Legacy Optuna wrapper (use `training_loop.run_optuna_search` instead)

**Factor System** (`factors/`)
- `base.py` — 16 base operators (rank, stddev, ts_mean, etc.)
- `registry.py` — AST scanning + lazy computation registry (452 factors in zoo/)
- `panel.py` — Wide panel transformation
- `scorer.py` — Factor scoring (ICIR + decay weighting, no turnover filter)
- `zoo/` — Factor library (gtja191/191 factors, alpha101/101, qlib158/154, academic/6)

**Scoring & Backtest**
- `models.py` — Core data models: `Signal` (frozen dataclass), `ScoredStock` (supports ml_score override)
- `screener.py` — `screen_universe()` — main screening logic with ML ranking
- `enhanced_backtest.py` — Backtesting engine with 3 trading strategies + tiered trailing stop
- `scoring/` — Category-capped scoring (100-point scale), adaptive weighting

### Data Flow

1. **Institutional Holdings Filter**: Fetch northbound + fund holdings → filter by ROE, PE, dividend yield
2. **Watchlist Merge**: Combine with user watchlist (stored in `.aimoon_watchlist.json`). **Watchlist stocks bypass all filters** (institutional holdings, market cap, price, etc.) as long as market data can be fetched.
3. **Factor Computation**: 452 Alpha Zoo factors → industry/size neutralization → percentile → z-score
4. **ML Prediction**: XGBoost + LightGBM ensemble → percentile ranking (0-100 score)
5. **Self-Learning**: ICIR dynamic weighting, factor decay detection, adaptive ensemble weights (24h-7d cache)
6. **Output**: Ranked results + CSV (with stop_loss/take_profit) + Markdown (with trading plan)

### Key Data Models

```python
# models.py
@dataclass(frozen=True)
class Signal:
    name: str           # Machine-readable (e.g., "ml_rank")
    label: str          # Human-readable (e.g., "ml_rank_80(强烈看多)")
    score: int          # -40 to +40

@dataclass(frozen=True)
class ScoredStock:
    code: str
    name: str
    ml_score: int | None   # ML model's percentile score (0-100), takes priority
    hybrid_score: int | None  # Hybrid score (0-100)
    signals: tuple[Signal, ...]
    # ... price, pe, pb, market_cap_yi, etc.
```

### Caching Strategy

- **Location**: `.aimoon_cache/` directory
- **Format**: All JSON (security fix: migrated from pickle to JSON)
- **ML Models**: 7-day TTL, `.aimoon_cache/ml/`
- **Adaptive Weights**: 24-hour TTL
- **ICIR Weights**: 7-day TTL
- **Factor Decay**: 7-day TTL
- **K-line Data**: 24-hour TTL

## Development Notes

### Python Version

- **Python 3.13**（由 uv 自动管理，无需手动安装）
- 使用现代类型提示语法：`int | None` 而非 `Optional[int]`
- uv 虚拟环境默认路径：`.venv/`

### Code Style

- **Immutability**: Use frozen dataclasses, avoid mutation
- **Type Hints**: Use Python 3.12+ syntax (`int | None`, `tuple[Signal, ...]`)
- **Formatting**: black (line-length=100) + ruff
- **Naming**: snake_case for functions/variables, PascalCase for classes

### Security Requirements

- **No Pickle**: All serialization uses JSON (CWE-502 fix)
- **No bare except**: Catch specific exceptions (`ValueError`, `KeyError`, etc.)
- **No hardcoded secrets**: Use environment variables or config files

### Testing & Quality

- **Static Analysis**: Run ruff, black, mypy, bandit before commits
- **No test suite yet**: Tests are planned (see README roadmap)
- **Coverage target**: 80% (when tests are added)

### Common Patterns

**Config Passing**: No global config singleton. Pass `Config` explicitly through function calls.

```python
from aimoon.config import Config

def my_function(cfg: Config, cache: DataCache) -> None:
    # Use cfg.history_days, cfg.cache_dir, etc.
```

**Cache Usage**: `DataCache` handles JSON serialization with TTL.

```python
from aimoon.cache import DataCache

cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)
# cache.get(key), cache.set(key, value), cache.clear()
```

**Factor Registration**: New factors are auto-discovered via AST scanning in `registry.py`.

```python
# factors/zoo/alpha101/alpha_001.py
from aimoon.factors.base import rank, stddev

@registry.register("alpha001", group="alpha101")
def alpha001(close, volume, returns):
    return rank(ts_argmax(close, 5))  # Example
```

**Error Handling**: Use Result type (in `result.py`) for data layer operations.

```python
from aimoon.result import Ok, Err, Result

result: Result[pd.DataFrame] = get_kline(code, days, cache)
if result.is_ok():
    kline = result.unwrap()
else:
    logger.error(result.unwrap_err())
```

## Key CLI Commands

```bash
aimoon                    # Full screening (ML ranking)
aimoon --demo             # Demo mode (no ML training, uses watchlist)
aimoon --top 10           # Top 10 stocks
aimoon --no-alpha         # Disable Alpha Zoo factors
aimoon watchlist add 600519,000858  # Add stocks to watchlist
aimoon watchlist list     # Show watchlist
aimoon cache clear        # Clear all caches
aimoon update             # Clear cache + re-fetch data
aimoon train-model        # Train ML models (incremental)
aimoon train-model --force # Force full re-training
aimoon backtest           # Run backtest
aimoon backtest --walk-forward  # Walk-forward validation
```

## Performance

- **Screening**: ~80 seconds for 81 stocks (first run), faster with cached factors
- **Backtest**: Walk-Forward validation with regime detection
- **Cache Hit**: Factor computation + K-line data cached (24h-7d TTL)
- **Code Quality**: Ruff 0 errors, Mypy 0 errors

## Related Documentation

- `README.md` — Full project documentation, trading strategy details, configuration reference
- `CODE_REVIEW_REPORT.md` — Security and code quality audit report
- `FIX_SUMMARY.md` — Summary of security fixes (pickle → JSON migration)
- `FINAL_OPTIMIZATION_COMPLETE.md` — Final optimization summary
- `PARAMETER_OPTIMIZATION_COMPLETE.md` — Parameter optimization details
- `LOOKAHEAD_BIAS_FIX_SUMMARY.md` — Look-ahead bias fixes

## Dependencies

- **Data Sources**: akshare, mootdx (A-share market data)
- **ML**: xgboost, lightgbm, scikit-learn
- **Data Processing**: pandas, numpy, scipy
- **CLI & Display**: rich, tabulate, colorama, pyyaml
- **Visualization**: matplotlib (for backtest charts)

## Important Files

- `src/aimoon/cli.py` — Entry point (27KB, complex command routing)
- `src/aimoon/screener.py` — Core screening logic
- `src/aimoon/enhanced_backtest.py` — Backtest engine (41KB, largest file)
- `src/aimoon/ml/ensemble.py` — ML ensemble predictor
- `src/aimoon/factors/registry.py` — Factor auto-discovery and registration
- `src/aimoon/models.py` — Core data models (Signal, ScoredStock)
- `src/aimoon/performance.py` — Performance optimization utilities
- `src/aimoon/regime_enhanced.py` — Enhanced regime detection
- `src/aimoon/rumi_strategy.py` — Rumi strategy with KRange adaptive exit
- `src/aimoon/rumi_optimizer.py` — Rumi parameter optimizer

## Recent Optimizations (2026-06-07)

### Data Pipeline Fixes
- **三级兜底数据源**: mootdx → Tencent → AKShare，个股和指数统一接口
- **fix_kline_dates 修复**: 整数索引数据自动生成日期范围，不再崩溃
- **原子缓存写入**: tempfile + os.replace()，崩溃不损坏缓存文件
- **实时行情 TTL**: 86400s → 300s（5分钟），确保交易时间内数据新鲜

### Backtest & ML Improvements
- **Walk-Forward Regime 检测**: Train/Test 分裂点检测市场状态，跨 regime 窗口自动跳过
- **数据泄漏修复**: Test 集不再包含 Train 日期
- **ML 模型简化**: max_depth 3→2, n_estimators 2000→500, lr 0.01→0.005, 更强正则化
- **因子质量过滤**: 移除换手率限制（原 0.30 阈值导致 452 因子全被过滤）
- **profit_protection 修复**: 增加 pnl > 0 条件，避免亏损仓误触发
- **IC 方向修复**: 改用 generate_labels 前瞻收益（原为 generate_realized_returns 过去收益）
- **自选股豁免**: watchlist 股票跳过所有过滤器，直接出现在结果中
- **feature_names.json 分离**: XGB/LGBM 使用独立特征名文件，避免竞争写入

### Bug Fixes
- **ConstantInputWarning**: 所有 spearmanr 调用点增加 std == 0 检查
- **ensemble.py 元 IC 丢弃**: 修复赋值语句
- **pct_change FutureWarning**: 17 个因子文件改用 fill_method=None
- **SkipAlpha 命名**: N818 → SkipAlphaError

### Code Quality
- Ruff: 540 → 0 错误
- Mypy: 153 → 0 错误

## Recent Optimizations (2026-06-05)

### Look-Ahead Bias Fixes (12 items)
- Fixed PurgedTimeSeriesSplit date-based purge
- Fixed label calculation to use opening prices
- Fixed backtest.py signal window
- Fixed enhanced_backtest entry price (T+1 open)
- Unified Alpha/technical signal time base
- Created generate_realized_returns for adapt_weights
- Fixed factor_decay to use realized returns
- Fixed ICIR weighter to use realized returns
- Fixed label_engine time offset
- Fixed momentum exit to use opening price
- Removed fake date sequences in history.py
- Fixed Kelly to use only historical trades

### Code Quality Improvements
- Fixed mypy type errors (5 → 0)
- Added type annotations to enhanced_backtest
- Extracted magic numbers to constants
- Refactored run_portfolio (520 → 80 lines)
- Created EnhancedPosition dataclass
- Added PhaseState dataclass

### Performance Optimizations
- Integrated performance optimization module
- Added parallel factor computation
- Implemented smart panel caching
- Added memory optimization
- Created performance monitoring

### Private Factor Library (5 factors, 25 sub-factors)
- proprietary_microstructure — Market microstructure factors
- proprietary_alternative — Alternative data factors
- proprietary_advanced_tech — Advanced technical factors
- proprietary_northbound — Northbound capital flow factors
- proprietary_sector_rotation — Sector rotation factors

### Enhanced Regime Detection
- Multi-dimensional market state identification (5 dimensions)
- 5 market states: bull, bear, sideways, high_volatility, crisis
- State transition probabilities
- Position scaling based on regime

### Parameter Optimizations
- Stop loss: 5% → 4%
- Take profit: 30% → 15%
- Trailing stop: +5% → +3% (breakeven), +10% → +6% (lock)
- Profit protection: 5% → 3% (peak), 1.5% → 1% (floor)
- Entry threshold: 55 → 60
- Hold days: 10 → 12
- Max positions: 5 → 4
- Stop loss cooldown: 15 → 20

### Performance Results
- Total Return: +38.99%
- Annual Return: +127.39%
- Sharpe Ratio: +5.13
- Max Drawdown: 11.57%
- Win Rate: 50.0%
- Profit Factor: 0.84
- Avg Win: +2.61%
- Avg Loss: -3.10%
- Trade Count: 18
- Avg Hold Days: 11

## Advanced Features

### Rumi Strategy with KRange Adaptive Exit
- Momentum + Relative Strength + Volatility scoring
- KRange-based adaptive exit mechanism
- Intelligent trailing stop
- Regime-aware position sizing

### Enhanced Regime Detection
- Multi-dimensional market state identification
- 5 market states with transition probabilities
- Position scaling based on regime
- Dynamic threshold adjustment

### Performance Optimization Module
- Parallel factor computation
- Smart panel caching
- Memory optimization
- Performance monitoring

### Private Factor Library
- 5 proprietary factor groups
- 25 sub-factors total
- Market microstructure factors
- Alternative data factors
- Advanced technical factors
- Northbound capital flow factors
- Sector rotation factors
