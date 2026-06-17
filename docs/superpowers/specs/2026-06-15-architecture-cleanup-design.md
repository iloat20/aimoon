# Architecture Cleanup Design: Layered Refactoring

**Date:** 2026-06-15
**Status:** Approved
**Approach:** Layered cleanup (bottom-up, 4 layers)

## Problem Statement

The aimoon codebase (588 files, ~40K lines) has accumulated architectural debt through rapid iteration:

- **8+ hardcoded cache paths** bypass `Config.cache_dir`
- **Pickle deserialization** in `data/filters.py` (CWE-502 security vulnerability)
- **3 parallel implementations** (backtest/enhanced_backtest, ensemble/ensemble_v2, regime/regime_enhanced/regime_ml)
- **God files** (ml/trainer.py 888 lines, ml/ensemble.py 805 lines)
- **Module boundary violations** (private imports, reverse dependencies)
- **Dead code** (DataManager, unused imports)

## Approach: Layered Cleanup

Refactor bottom-up in 4 layers, each solving a category of problems. One-shot execution.

---

## Layer 1: Infrastructure — Unified Cache System

### Goal
All modules use `Config.cache_dir` through a consistent `DataCache` interface. No hardcoded paths.

### Changes

#### 1.1 Delete `data/manager.py`
- `DataManager` is dead code (never used by main pipeline)
- Remove from `data/__init__.py` exports

#### 1.2 Parameterize cache paths in ML modules
Each module receives `cache_dir: Path` instead of hardcoding `.aimoon_cache`:

| Module | Current Hardcoded Path | Change |
|--------|----------------------|--------|
| `ml/ensemble.py:28` | `Path(__file__).resolve().parent.parent.parent.parent / ".aimoon_cache" / "ml"` | Accept `cache_dir` param |
| `ml/ensemble_v2.py:27` | `Path(".aimoon_cache") / "ml"` | **DELETE FILE** |
| `ml/trainer.py:46` | `Path(".aimoon_cache") / "ml"` | Accept `cache_dir` param |
| `ml/icir_weighter.py:27` | `Path(".aimoon_cache") / "icir"` | Accept `cache_dir` param |
| `ml/factor_decay.py:24` | `Path(".aimoon_cache") / "factor_decay"` | Accept `cache_dir` param |
| `ml/factor_quality.py:33` | `Path(".aimoon_cache") / "factor_quality"` | Accept `cache_dir` param |
| `ml/predictor.py:18` | `Path(".aimoon_cache") / "ml"` | Accept `cache_dir` param |
| `ml/hyperopt.py:46` | `Path(".aimoon_cache") / "ml"` | Accept `cache_dir` param |
| `data/filters.py:20` | `Path(".aimoon_cache")` | Accept `cache_dir` param |
| `data/spot.py:21` | `Path(".aimoon_cache") / "_spot.json"` | Accept `cache_dir` param |

#### 1.3 Update callers
All callers pass `cfg.cache_dir` to the newly parameterized functions:
- `screener.py` → passes `cache_dir` to `ml/ensemble.py`, `factors/`
- `cli.py` → passes `cache_dir` to `data/filters.py`, `data/spot.py`
- `ml/trainer.py` → passes `cache_dir` to sub-modules

#### 1.4 Thread-safety fix
- `data/manager.py:377` global `_manager` — **DELETED** with the file

---

## Layer 2: Data — Security & Boundaries

### Goal
Eliminate pickle security vulnerability. Clean module boundaries.

### Changes

#### 2.1 Fix pickle → JSON in `data/filters.py`
Replace all `pickle.loads/dumps` with `json.loads/dumps`:

```python
# Lines to change: 33, 39, 66, 77, 84, 105, 120
# Before:
pickle.loads(path.read_bytes())
path.write_bytes(pickle.dumps(result))

# After:
json.loads(path.read_text(encoding="utf-8"))
path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
```

#### 2.2 Export private members from `data/spot.py`
Promote to public API:

```python
# data/spot.py — add at module level:
DEFAULT_HEADERS = _DEFAULT_HEADERS  # line 15
em_get = _em_get                    # line 30
FIELDS = _FIELDS                    # line 22
```

Update `data/filters.py` imports:
```python
# Before:
from aimoon.data.spot import _DEFAULT_HEADERS, _em_get, _FIELDS

# After:
from aimoon.data.spot import DEFAULT_HEADERS, em_get, FIELDS
```

#### 2.3 Split `data/filters.py` (566 lines → 3 files)

| New File | Responsibility | Lines |
|----------|---------------|-------|
| `data/holdings_pool.py` | `get_holdings_pool()`, `_build_holdings_pool()`, `_get_northbound()`, `_filter_by_*()` functions | ~250 |
| `data/filters.py` | `filter_universe()`, `pre_sort_universe()` | ~120 |
| `data/sector.py` | `get_sector_context()`, `filter_by_sectors()` | ~100 |

#### 2.4 Move `fix_kline_dates` to `data/validator.py`
- Move function from `data/history.py` to `data/validator.py`
- Update import in `factors/panel.py:45`
- Update all internal callers in `data/history.py`

---

## Layer 3: Core — Split & Delete

### Goal
Eliminate parallel implementations. Split god files.

### Changes

#### 3.1 Delete parallel implementations

**Critical**: `enhanced_backtest/` has 6 module-level imports from `backtest/` (risk_controls, position, _detect_regime_safe). Must migrate shared code first.

**Step 1: Migrate shared code from `backtest/` to `enhanced_backtest/`**

| Source | Target | What |
|--------|--------|------|
| `backtest/risk_controls.py` (125 lines) | `enhanced_backtest/risk_controls.py` | 20 constants + 4 functions (trailing stop, ATR, etc.) |
| `backtest/position.py` (122 lines) | `enhanced_backtest/position.py` | `compute_position_weights()` |
| `backtest/engine.py:_detect_regime_safe` | `regime_enhanced.py` | Reimplement as wrapper or move |

**Step 2: Update `enhanced_backtest/` imports (6 files)**

| File | Current Import | New Import |
|------|---------------|------------|
| `portfolio_runner.py:16` | `from aimoon.backtest import _detect_regime_safe, risk_controls` | `from aimoon.enhanced_backtest import risk_controls` + `from aimoon.regime_enhanced import detect_regime` |
| `engine.py:13` | `from aimoon.backtest import risk_controls` | `from aimoon.enhanced_backtest import risk_controls` |
| `engine.py:14` | `from aimoon.backtest.position import compute_position_weights` | `from aimoon.enhanced_backtest.position import compute_position_weights` |
| `exit_rules.py:15` | `from aimoon.backtest import risk_controls` | `from aimoon.enhanced_backtest import risk_controls` |
| `exit_rules.py:16` | `from aimoon.backtest.risk_controls import PARTIAL_PROFIT_*` | `from aimoon.enhanced_backtest.risk_controls import PARTIAL_PROFIT_*` |
| `helpers.py:12` | `from aimoon.backtest import risk_controls` | `from aimoon.enhanced_backtest import risk_controls` |
| `entry_rules.py:14` | `from aimoon.backtest.position import compute_position_weights` | `from aimoon.enhanced_backtest.position import compute_position_weights` |

**Step 3: Delete**

| File | Reason | Replacement |
|------|--------|-------------|
| `backtest/` (remaining files: engine.py, metrics.py, bt_engine.py, __init__.py) | Old backtest engine, superseded | `enhanced_backtest/` |
| `ml/ensemble_v2.py` | Incomplete migration | `ml/ensemble.py` |
| `regime.py` | Superseded by `regime_enhanced.py` | `regime_enhanced.py` |
| `regime_ml.py` | Dead module (zero importers) | `regime_enhanced.py` |
| `ml/ensemble.py:compute_optimal_weights()` | Only used by `ensemble_v2.py` | Removed |

**Step 4: Update other callers**

| File | Change |
|------|--------|
| `cli.py:515` | `from aimoon.regime import detect_regime` → `from aimoon.regime_enhanced import detect_regime` |
| `output.py:17` | Remove `TYPE_CHECKING` import of `PortfolioBacktest` (dead type ref) |
| `tests/test_backtest.py` | Rewrite to test `enhanced_backtest` equivalents or delete |
| `tests/test_portfolio_backtest.py` | Same |
| `tests/test_regime.py` | `from aimoon.regime import ...` → `from aimoon.regime_enhanced import ...` |

#### 3.2 Split `ml/trainer.py` (888 lines → 4 files)

| New File | Responsibility | Lines |
|----------|---------------|-------|
| `ml/trainer.py` | `train_model()`, `train_ensemble()`, `ensure_model_fresh()` (public API only) | ~200 |
| `ml/data_collection.py` | `_collect_training_data()`, `_select_dates_evenly()` | ~200 |
| `ml/training_loop.py` | CV loop, warm start, overfit detection, auto-degradation | ~300 |
| `ml/model_persistence.py` | `save_model_artifacts()`, `load_model_artifacts()` | ~150 |

#### 3.3 Split `ml/ensemble.py` (805 lines → 3 files)

| New File | Responsibility | Lines |
|----------|---------------|-------|
| `ml/ensemble.py` | `EnsemblePredictor` class, `ensemble_predict_signals()` | ~350 |
| `ml/stacking.py` | `StackingEnsemble` class | ~300 |
| `ml/ensemble_signals.py` | Signal threshold logic, percentile mapping | ~100 |

#### 3.4 Fix `models.py` reverse dependency
Remove the lazy `from aimoon.scoring import hybrid_score` inside `ScoredStock.total_score` property.

**Approach**: Make `total_score` a regular field (computed by caller, not by the model):

```python
# models.py — remove lazy import
@dataclass(frozen=True)
class ScoredStock:
    ...
    total_score: int = 0  # Computed by scoring layer, passed at construction
```

**Callers that construct ScoredStock and must compute total_score**:
- `screener.py:screen_stock()` — compute `hybrid_score(signals)` before constructing
- `scoring/__init__.py:collect_signals()` — compute before returning
- `scoring/__init__.py:score_portfolio()` — compute before returning
- Any other `ScoredStock(...)` construction site

This eliminates the `models.py → scoring/` reverse dependency entirely.

---

## Layer 4: Integration — Boundaries & Cleanup

### Goal
Clean module boundaries, eliminate magic, standardize patterns.

### Changes

#### 4.1 Split `scoring/__init__.py` (177 lines → 2 files)

| New File | Responsibility | Lines |
|----------|---------------|-------|
| `scoring/__init__.py` | `__all__` exports, `collect_signals()` registry | ~50 |
| `scoring/portfolio.py` | `score_portfolio()`, `hybrid_score()` | ~120 |

#### 4.2 Optimize `screener.py` — merge panel builds
```python
# Before: panel built twice
_inject_alpha_signals(results, all_klines)  # builds panel
_inject_ml_signals(results, all_klines)     # builds panel again

# After: single panel build
panel = build_panel(all_klines)
add_all_indicators_batch(panel)
_inject_alpha_signals(results, panel)  # reuse panel
_inject_ml_signals(results, panel)     # reuse panel
```

#### 4.3 Clean `cli.py` lazy imports
Move lazy imports to function entry points (not scattered in function bodies):

```python
# Before (scattered):
def _run_backtest(args, cfg, fmt):
    ...
    from aimoon.enhanced_backtest import EnhancedBacktestEngine  # line 200
    ...
    from aimoon.scoring.rps import compute_rps  # line 230

# After (grouped at entry):
def _run_backtest(args, cfg, fmt):
    from aimoon.enhanced_backtest import EnhancedBacktestEngine
    from aimoon.scoring.rps import compute_rps
    from aimoon.risk import RiskLimits, check_risk_limits
    ...
```

#### 4.4 Standardize error handling
| Layer | Pattern | Change |
|-------|---------|--------|
| Data layer | `Result[T, E]` | No change |
| ML layer | `except Exception:` | Convert to `Result[T, E]` or specific exceptions |
| Scoring layer | `None` for failures | No change |
| CLI layer | `sys.exit(1)` | No change |

#### 4.5 Delete dead code
| File | Reason |
|------|--------|
| `factors/scorer.py:15` — `_greedy_correlation_filter` import | Unused (noqa: F401) |
| `metrics.py:10` — self-referential import | Bug or dead code |

---

## Dependency Graph (After Refactoring)

```
                    cli.py
                   / |   \
                  /  |    \
           screener output  regime_enhanced
          / |   \      \        |
         /  |    \      \       |
    scoring factors ml  risk  indicators
       |      |     |     |
  indicators base  feature_  config
                  pipeline
```

**Key changes:**
- `models.py` no longer depends on `scoring/` (reverse dependency eliminated)
- `backtest/` deleted — `risk_controls` and `position` moved into `enhanced_backtest/`
- `factors/panel.py` no longer depends on `data/history.py` (fix_kline_dates moved to data/validator.py)
- `data/filters.py` no longer imports private members from `data/spot.py`
- `regime.py` and `regime_ml.py` deleted — `regime_enhanced.py` is sole implementation

---

## Verification Plan

After each layer, run:
```bash
ruff check src/aimoon
mypy src/aimoon --ignore-missing-imports
black --check src/aimoon
aimoon --demo  # Verify main pipeline still works
```

After all layers:
```bash
aimoon train-model --force  # Verify ML pipeline
aimoon backtest             # Verify backtest pipeline
```

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Breaking `backtest/` callers | High | Grep for all `from aimoon.backtest` imports before deletion |
| `models.py` change breaks scoring | Medium | Keep `total_score` as field, update all constructors |
| Cache path changes break ML loading | Medium | Add backward-compat: check old paths if new path empty |
| `fix_kline_dates` move breaks panel | Low | Update import in `factors/panel.py` |

---

## Files Modified (Estimated)

| Layer | Files Modified | Files Deleted | Files Created |
|-------|---------------|---------------|---------------|
| 1. Infrastructure | 10 | 1 | 0 |
| 2. Data | 5 | 0 | 3 |
| 3. Core | 12 | 6 | 7 |
| 4. Integration | 6 | 0 | 1 |
| **Total** | **33** | **7** | **11** |
