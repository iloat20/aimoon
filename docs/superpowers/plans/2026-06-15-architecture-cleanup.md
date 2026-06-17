# Architecture Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate architectural debt: unify cache system, fix pickle security vulnerability, delete parallel implementations, split god files, and clean module boundaries.

**Architecture:** Bottom-up layered refactoring in 4 layers. Each layer is self-contained and verified independently before proceeding.

**Tech Stack:** Python 3.12+, pandas, xgboost, lightgbm, akshare, httpx, rich

---

## Layer 1: Infrastructure — Unified Cache System

### Task 1: Delete dead DataManager

**Files:**
- Delete: `src/aimoon/data/manager.py`
- Modify: `src/aimoon/data/__init__.py`

- [ ] **Step 1: Remove DataManager from data/__init__.py**

```python
# src/aimoon/data/__init__.py
"""数据获取层"""

from aimoon.data.filters import (
    filter_by_sectors,
    filter_universe,
    get_holdings_pool,
    get_sector_context,
)
from aimoon.data.history import get_kline
from aimoon.cache import DataCache
from aimoon.data.spot import get_spot, get_spot_for_codes

__all__ = [
    "get_spot",
    "get_spot_for_codes",
    "get_kline",
    "filter_universe",
    "filter_by_sectors",
    "get_sector_context",
    "get_holdings_pool",
    "DataCache",
]
```

- [ ] **Step 2: Delete data/manager.py**

Run: `del src\aimoon\data\manager.py`

- [ ] **Step 3: Verify no imports break**

Run: `python -c "from aimoon.data import get_kline, get_spot, filter_universe, DataCache"`
Expected: No error

- [ ] **Step 4: Run linter**

Run: `ruff check src/aimoon/data/`
Expected: 0 errors

- [ ] **Step 5: Commit**

```bash
git add src/aimoon/data/__init__.py src/aimoon/data/manager.py
git commit -m "refactor: delete dead DataManager, clean data package exports"
```

---

### Task 2: Parameterize ml/ensemble.py cache path

**Files:**
- Modify: `src/aimoon/ml/ensemble.py`

- [ ] **Step 1: Read current hardcoded cache path**

The current code at line 28:
```python
_CACHE_DIR = Path(__file__).resolve().parent.parent.parent.parent / ".aimoon_cache" / "ml"
```

- [ ] **Step 2: Add cache_dir parameter to EnsemblePredictor**

Find the `__init__` method of `EnsemblePredictor` and add `cache_dir: Path | None = None` parameter. If None, use a default.

Find the `from_cache` classmethod and add `cache_dir: Path` parameter.

Replace all `_CACHE_DIR` references with `self._cache_dir` (instance attribute).

```python
class EnsemblePredictor:
    def __init__(self, xgb_model, lgbm_model, en_model, feature_names, weights, cache_dir: Path):
        ...
        self._cache_dir = cache_dir

    @classmethod
    def from_cache(cls, cache_dir: Path) -> "EnsemblePredictor | None":
        ml_dir = cache_dir / "ml"
        ...
```

- [ ] **Step 3: Update callers of EnsemblePredictor.from_cache**

Search for `EnsemblePredictor.from_cache` and add `cache_dir` argument:

```
grep -rn "EnsemblePredictor.from_cache" src/aimoon/
```

Update each call site to pass `cache_dir` (from Config or parent caller).

- [ ] **Step 4: Verify**

Run: `ruff check src/aimoon/ml/ensemble.py`
Run: `python -c "from aimoon.ml.ensemble import EnsemblePredictor"`

- [ ] **Step 5: Commit**

```bash
git add src/aimoon/ml/ensemble.py
git commit -m "refactor: parameterize EnsemblePredictor cache_dir"
```

---

### Task 3: Parameterize ml/trainer.py cache path

**Files:**
- Modify: `src/aimoon/ml/trainer.py`

- [ ] **Step 1: Replace hardcoded _MODEL_DIR**

Current at line 46:
```python
_MODEL_DIR = Path(".aimoon_cache") / "ml"
```

Add `cache_dir: Path` parameter to `train_model()`, `train_ensemble()`, and `ensure_model_fresh()`. Use `cache_dir / "ml"` instead of `_MODEL_DIR`.

- [ ] **Step 2: Update callers**

Search for calls to `train_model(`, `train_ensemble(`, `ensure_model_fresh(` and add `cache_dir` argument.

- [ ] **Step 3: Verify and commit**

```bash
ruff check src/aimoon/ml/trainer.py
git add src/aimoon/ml/trainer.py
git commit -m "refactor: parameterize ml/trainer cache paths"
```

---

### Task 4: Parameterize remaining ML cache paths

**Files:**
- Modify: `src/aimoon/ml/icir_weighter.py`
- Modify: `src/aimoon/ml/factor_decay.py`
- Modify: `src/aimoon/ml/factor_quality.py`
- Modify: `src/aimoon/ml/predictor.py`
- Modify: `src/aimoon/ml/hyperopt.py`

- [ ] **Step 1: Update icir_weighter.py**

Replace `_ICIR_CACHE_DIR = Path(".aimoon_cache") / "icir"` with `cache_dir: Path` parameter on public functions.

- [ ] **Step 2: Update factor_decay.py**

Replace `_DECAY_CACHE_DIR = Path(".aimoon_cache") / "factor_decay"` with `cache_dir: Path` parameter.

- [ ] **Step 3: Update factor_quality.py**

Replace `_FILTER_CACHE_DIR = Path(".aimoon_cache") / "factor_quality"` with `cache_dir: Path` parameter.

- [ ] **Step 4: Update predictor.py**

Replace `_FEATURE_CACHE_DIR = Path(".aimoon_cache") / "ml"` with `cache_dir: Path` parameter.

- [ ] **Step 5: Update hyperopt.py**

Replace `HYPEROPT_CACHE_DIR = Path(".aimoon_cache") / "ml"` with `cache_dir: Path` parameter.

- [ ] **Step 6: Update all callers**

For each module, search for its public function calls and add `cache_dir` argument. The call chain is:
```
cli.py → screener.py → ml/ensemble.py → ml/icir_weighter.py, ml/factor_decay.py
cli.py → ml/trainer.py → ml/hyperopt.py, ml/predictor.py
```

- [ ] **Step 7: Verify and commit**

```bash
ruff check src/aimoon/ml/
python -c "from aimoon.ml import ensemble, trainer, icir_weighter, factor_decay, predictor, hyperopt"
git add src/aimoon/ml/
git commit -m "refactor: parameterize all ML module cache paths"
```

---

### Task 5: Parameterize data layer cache paths

**Files:**
- Modify: `src/aimoon/data/filters.py`
- Modify: `src/aimoon/data/spot.py`

- [ ] **Step 1: Update data/spot.py**

Replace `_SPOT_CACHE_FILE = Path(".aimoon_cache") / "_spot.json"` with `cache_dir: Path` parameter on `get_spot()` and `get_spot_for_codes()`.

```python
def get_spot(cfg: Config, cache_dir: Path | None = None) -> Result[pd.DataFrame, str]:
    cache_dir = cache_dir or Path(cfg.cache_dir)
    spot_file = cache_dir / "_spot.json"
    ...
```

- [ ] **Step 2: Update data/filters.py**

Replace `_CACHE_DIR = Path(".aimoon_cache")` with `cache_dir: Path` parameter on `get_holdings_pool()`.

```python
def get_holdings_pool(cfg: Config, force: bool = False, cache_dir: Path | None = None) -> set[str]:
    cache_dir = cache_dir or Path(cfg.cache_dir)
    ...
```

- [ ] **Step 3: Update callers**

`cli.py` calls `get_holdings_pool(cfg)` and `get_spot(cfg)` — pass `cfg.cache_dir`.

- [ ] **Step 4: Verify and commit**

```bash
ruff check src/aimoon/data/
python -c "from aimoon.data import get_spot, get_holdings_pool"
git add src/aimoon/data/filters.py src/aimoon/data/spot.py
git commit -m "refactor: parameterize data layer cache paths"
```

---

### Task 6: Layer 1 verification

- [ ] **Step 1: Run full lint**

Run: `ruff check src/aimoon`
Expected: 0 errors (or only pre-existing errors)

- [ ] **Step 2: Run type check**

Run: `mypy src/aimoon --ignore-missing-imports`
Expected: No new errors

- [ ] **Step 3: Run demo**

Run: `aimoon --demo`
Expected: Pipeline completes without error

- [ ] **Step 4: Commit if needed**

---

## Layer 2: Data — Security & Boundaries

### Task 7: Fix pickle → JSON in data/filters.py

**Files:**
- Modify: `src/aimoon/data/filters.py`

- [ ] **Step 1: Read the file and identify all pickle usage**

Lines using pickle: 5 (import), 33, 39, 66, 77, 84, 105, 120

- [ ] **Step 2: Replace pickle import with json**

```python
# Before:
import pickle

# After:
import json
```

- [ ] **Step 3: Replace all pickle.loads/dumps**

For each occurrence, replace:
```python
# Before:
pickle.loads(path.read_bytes())
path.write_bytes(pickle.dumps(result))

# After:
json.loads(path.read_text(encoding="utf-8"))
path.write_text(json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8")
```

Note: `default=str` handles non-JSON-serializable types (like datetime).

- [ ] **Step 4: Verify the holdings pool data is JSON-compatible**

The pool is a `set[str]` — JSON serializable. Check that the cache file format is compatible.

- [ ] **Step 5: Verify and commit**

```bash
python -c "from aimoon.data.filters import get_holdings_pool"
ruff check src/aimoon/data/filters.py
git add src/aimoon/data/filters.py
git commit -m "fix: replace pickle with JSON in filters.py (CWE-502)"
```

---

### Task 8: Export private members from data/spot.py

**Files:**
- Modify: `src/aimoon/data/spot.py`
- Modify: `src/aimoon/data/filters.py`

- [ ] **Step 1: Add public aliases in data/spot.py**

At the bottom of `data/spot.py`, add:
```python
# Public aliases for internal helpers used by other modules
DEFAULT_HEADERS = _DEFAULT_HEADERS
em_get = _em_get
FIELDS = _FIELDS
```

- [ ] **Step 2: Update data/filters.py imports**

```python
# Before:
from aimoon.data.spot import _DEFAULT_HEADERS, _em_get, _FIELDS

# After:
from aimoon.data.spot import DEFAULT_HEADERS, em_get, FIELDS
```

Also update all references to `_DEFAULT_HEADERS`, `_em_get`, `_FIELDS` in the file body.

- [ ] **Step 3: Verify and commit**

```bash
ruff check src/aimoon/data/
git add src/aimoon/data/spot.py src/aimoon/data/filters.py
git commit -m "refactor: export private spot.py members as public API"
```

---

### Task 9: Split data/filters.py into 3 files

**Files:**
- Create: `src/aimoon/data/holdings_pool.py`
- Create: `src/aimoon/data/sector.py`
- Modify: `src/aimoon/data/filters.py`
- Modify: `src/aimoon/data/__init__.py`

- [ ] **Step 1: Create data/holdings_pool.py**

Move from `data/filters.py`:
- `get_holdings_pool()` (the public function)
- `_build_holdings_pool()`
- `_get_northbound()`
- `_filter_by_listing()`
- `_filter_by_roe()`
- `_filter_by_fund_pct()`
- `_filter_by_pe()`
- `_filter_by_dividend_yield()`
- `save_shipped_pool()`
- `_POOL_FILE`, `_POOL_TTL`, `_SHIPPED_POOL` constants

```python
"""机构持仓池构建。

从北向资金 + 基金持仓 + 财务指标筛选机构关注股票池。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from aimoon.config import Config

logger = logging.getLogger(__name__)

# ... (moved constants and functions)
```

- [ ] **Step 2: Create data/sector.py**

Move from `data/filters.py`:
- `get_sector_context()`
- `filter_by_sectors()`

```python
"""板块过滤。"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

# ... (moved functions)
```

- [ ] **Step 3: Slim down data/filters.py**

Keep only:
- `filter_universe()`
- `pre_sort_universe()`

Update imports to use the new modules.

- [ ] **Step 4: Update data/__init__.py**

```python
from aimoon.data.holdings_pool import get_holdings_pool
from aimoon.data.sector import filter_by_sectors, get_sector_context
from aimoon.data.filters import filter_universe
```

- [ ] **Step 5: Update all callers**

Search for `from aimoon.data.filters import` and update to use new module paths.

- [ ] **Step 6: Verify and commit**

```bash
ruff check src/aimoon/data/
python -c "from aimoon.data import get_holdings_pool, filter_universe, filter_by_sectors"
git add src/aimoon/data/
git commit -m "refactor: split data/filters.py into holdings_pool, filters, sector"
```

---

### Task 10: Move fix_kline_dates to data/validator.py

**Files:**
- Modify: `src/aimoon/data/history.py`
- Modify: `src/aimoon/data/validator.py`
- Modify: `src/aimoon/factors/panel.py`

- [ ] **Step 1: Move fix_kline_dates from history.py to validator.py**

Cut the function from `data/history.py` and paste into `data/validator.py`.

- [ ] **Step 2: Add re-export in data/history.py**

```python
from aimoon.data.validator import fix_kline_dates  # re-export for backward compat
```

- [ ] **Step 3: Update factors/panel.py import**

```python
# Before:
from aimoon.data.history import fix_kline_dates

# After:
from aimoon.data.validator import fix_kline_dates
```

- [ ] **Step 4: Verify and commit**

```bash
ruff check src/aimoon/data/ src/aimoon/factors/panel.py
python -c "from aimoon.data.history import fix_kline_dates; from aimoon.data.validator import fix_kline_dates"
git add src/aimoon/data/history.py src/aimoon/data/validator.py src/aimoon/factors/panel.py
git commit -m "refactor: move fix_kline_dates to data/validator.py"
```

---

### Task 11: Layer 2 verification

- [ ] **Step 1: Run full lint**

Run: `ruff check src/aimoon`

- [ ] **Step 2: Run demo**

Run: `aimoon --demo`

- [ ] **Step 3: Commit if needed**

---

## Layer 3: Core — Split & Delete

### Task 12: Migrate backtest/risk_controls.py to enhanced_backtest/

**Files:**
- Create: `src/aimoon/enhanced_backtest/risk_controls.py` (copy from backtest/)
- Create: `src/aimoon/enhanced_backtest/position.py` (copy from backtest/)

- [ ] **Step 1: Copy backtest/risk_controls.py to enhanced_backtest/**

```bash
copy src\aimoon\backtest\risk_controls.py src\aimoon\enhanced_backtest\risk_controls.py
```

- [ ] **Step 2: Copy backtest/position.py to enhanced_backtest/**

```bash
copy src\aimoon\backtest\position.py src\aimoon\enhanced_backtest\position.py
```

- [ ] **Step 3: Verify the copied files are valid Python**

```bash
python -c "from aimoon.enhanced_backtest.risk_controls import TRAILING_STOP_TIERS, get_atr_value"
python -c "from aimoon.enhanced_backtest.position import compute_position_weights"
```

- [ ] **Step 4: Commit**

```bash
git add src/aimoon/enhanced_backtest/risk_controls.py src/aimoon/enhanced_backtest/position.py
git commit -m "refactor: migrate risk_controls and position to enhanced_backtest/"
```

---

### Task 13: Update enhanced_backtest/ imports from backtest/

**Files:**
- Modify: `src/aimoon/enhanced_backtest/portfolio_runner.py`
- Modify: `src/aimoon/enhanced_backtest/engine.py`
- Modify: `src/aimoon/enhanced_backtest/exit_rules.py`
- Modify: `src/aimoon/enhanced_backtest/helpers.py`
- Modify: `src/aimoon/enhanced_backtest/entry_rules.py`

- [ ] **Step 1: Update portfolio_runner.py**

```python
# Before (line 16):
from aimoon.backtest import _detect_regime_safe, risk_controls

# After:
from aimoon.enhanced_backtest import risk_controls
from aimoon.regime_enhanced import detect_regime as _detect_regime_safe
```

Also update any calls to `_detect_regime_safe()` to use `detect_regime()` with the correct signature.

- [ ] **Step 2: Update engine.py**

```python
# Before (lines 13-14):
from aimoon.backtest import risk_controls
from aimoon.backtest.position import compute_position_weights

# After:
from aimoon.enhanced_backtest import risk_controls
from aimoon.enhanced_backtest.position import compute_position_weights
```

- [ ] **Step 3: Update exit_rules.py**

```python
# Before (lines 15-16):
from aimoon.backtest import risk_controls
from aimoon.backtest.risk_controls import PARTIAL_PROFIT_TAKE_PNL, PARTIAL_PROFIT_TAKE_RATIO, PARTIAL_PROFIT_SECONDARY_PNL

# After:
from aimoon.enhanced_backtest import risk_controls
from aimoon.enhanced_backtest.risk_controls import PARTIAL_PROFIT_TAKE_PNL, PARTIAL_PROFIT_TAKE_RATIO, PARTIAL_PROFIT_SECONDARY_PNL
```

- [ ] **Step 4: Update helpers.py**

```python
# Before (line 12):
from aimoon.backtest import risk_controls

# After:
from aimoon.enhanced_backtest import risk_controls
```

- [ ] **Step 5: Update entry_rules.py**

```python
# Before (line 14):
from aimoon.backtest.position import compute_position_weights

# After:
from aimoon.enhanced_backtest.position import compute_position_weights
```

- [ ] **Step 6: Verify all enhanced_backtest imports work**

```bash
python -c "from aimoon.enhanced_backtest import EnhancedBacktestEngine"
python -c "from aimoon.enhanced_backtest.portfolio_runner import run_portfolio"
```

- [ ] **Step 7: Commit**

```bash
git add src/aimoon/enhanced_backtest/
git commit -m "refactor: update enhanced_backtest imports to use local risk_controls"
```

---

### Task 14: Update output.py and cli.py callers

**Files:**
- Modify: `src/aimoon/output.py`
- Modify: `src/aimoon/cli.py`

- [ ] **Step 1: Update output.py**

Remove or rewrite the `TYPE_CHECKING` import of `PortfolioBacktest` (line 17):

```python
# Before:
if TYPE_CHECKING:
    from aimoon.backtest import PortfolioBacktest

# After: remove this block entirely (PortfolioBacktest is no longer needed for type checking)
```

- [ ] **Step 2: Update cli.py**

```python
# Before (line 515):
from aimoon.regime import detect_regime

# After:
from aimoon.regime_enhanced import detect_regime
```

- [ ] **Step 3: Verify and commit**

```bash
ruff check src/aimoon/output.py src/aimoon/cli.py
git add src/aimoon/output.py src/aimoon/cli.py
git commit -m "refactor: update output.py and cli.py to remove backtest/regime imports"
```

---

### Task 15: Delete old backtest/ and regime modules

**Files:**
- Delete: `src/aimoon/backtest/` (entire package)
- Delete: `src/aimoon/regime.py`
- Delete: `src/aimoon/regime_ml.py`
- Delete: `src/aimoon/ml/ensemble_v2.py`

- [ ] **Step 1: Final check for any remaining backtest imports**

```bash
grep -rn "from aimoon.backtest" src/aimoon/ --include="*.py"
```

Expected: Only `enhanced_backtest/` files (which we already updated).

- [ ] **Step 2: Delete the packages/files**

```bash
rmdir /s /q src\aimoon\backtest
del src\aimoon\regime.py
del src\aimoon\regime_ml.py
del src\aimoon\ml\ensemble_v2.py
```

- [ ] **Step 3: Update tests**

Delete or update:
- `tests/test_backtest.py` — delete or rewrite to test enhanced_backtest
- `tests/test_portfolio_backtest.py` — delete or rewrite
- `tests/test_regime.py` — update import to `from aimoon.regime_enhanced import ...`

- [ ] **Step 4: Verify no import errors**

```bash
python -c "from aimoon.cli import main"
python -c "from aimoon.enhanced_backtest import EnhancedBacktestEngine"
```

- [ ] **Step 5: Commit**

```bash
git add -A src/aimoon/backtest/ src/aimoon/regime.py src/aimoon/regime_ml.py src/aimoon/ml/ensemble_v2.py tests/
git commit -m "refactor: delete old backtest/, regime.py, regime_ml.py, ensemble_v2.py"
```

---

### Task 16: Split ml/trainer.py into 4 files

**Files:**
- Create: `src/aimoon/ml/data_collection.py`
- Create: `src/aimoon/ml/training_loop.py`
- Create: `src/aimoon/ml/model_persistence.py`
- Modify: `src/aimoon/ml/trainer.py`

- [ ] **Step 1: Read ml/trainer.py and identify function boundaries**

Read the full file and map out which functions go where:
- `ml/trainer.py` keeps: `train_model()`, `train_ensemble()`, `ensure_model_fresh()`, `TrainingResult`, `EnsembleTrainingResult`
- `ml/data_collection.py` gets: `_collect_training_data()`, `_select_dates_evenly()`
- `ml/training_loop.py` gets: CV loop logic, warm start, overfit detection
- `ml/model_persistence.py` gets: `save_model_artifacts()`, `load_model_artifacts()`

- [ ] **Step 2: Create ml/data_collection.py**

Move `_collect_training_data()` and `_select_dates_evenly()` to the new file.

- [ ] **Step 3: Create ml/training_loop.py**

Extract the CV training loop from `train_model()` into a standalone function.

- [ ] **Step 4: Create ml/model_persistence.py**

Extract model save/load functions.

- [ ] **Step 5: Update ml/trainer.py imports**

```python
from aimoon.ml.data_collection import _collect_training_data, _select_dates_evenly
from aimoon.ml.training_loop import run_cv_training
from aimoon.ml.model_persistence import save_model_artifacts, load_model_artifacts
```

- [ ] **Step 6: Verify**

```bash
python -c "from aimoon.ml.trainer import train_model, train_ensemble"
ruff check src/aimoon/ml/trainer.py src/aimoon/ml/data_collection.py src/aimoon/ml/training_loop.py src/aimoon/ml/model_persistence.py
```

- [ ] **Step 7: Commit**

```bash
git add src/aimoon/ml/
git commit -m "refactor: split ml/trainer.py into trainer, data_collection, training_loop, model_persistence"
```

---

### Task 17: Split ml/ensemble.py into 3 files

**Files:**
- Create: `src/aimoon/ml/stacking.py`
- Create: `src/aimoon/ml/ensemble_signals.py`
- Modify: `src/aimoon/ml/ensemble.py`

- [ ] **Step 1: Read ml/ensemble.py and identify class boundaries**

- `ml/ensemble.py` keeps: `EnsemblePredictor` class
- `ml/stacking.py` gets: `StackingEnsemble` class
- `ml/ensemble_signals.py` gets: `ensemble_predict_signals()` function

- [ ] **Step 2: Create ml/stacking.py**

Move `StackingEnsemble` class to the new file.

- [ ] **Step 3: Create ml/ensemble_signals.py**

Move `ensemble_predict_signals()` to the new file.

- [ ] **Step 4: Update ml/ensemble.py imports**

```python
from aimoon.ml.stacking import StackingEnsemble  # re-export for backward compat
from aimoon.ml.ensemble_signals import ensemble_predict_signals  # re-export
```

- [ ] **Step 5: Verify**

```bash
python -c "from aimoon.ml.ensemble import EnsemblePredictor, ensemble_predict_signals, StackingEnsemble"
ruff check src/aimoon/ml/ensemble.py src/aimoon/ml/stacking.py src/aimoon/ml/ensemble_signals.py
```

- [ ] **Step 6: Commit**

```bash
git add src/aimoon/ml/
git commit -m "refactor: split ml/ensemble.py into ensemble, stacking, ensemble_signals"
```

---

### Task 18: Fix models.py reverse dependency

**Files:**
- Modify: `src/aimoon/models.py`
- Modify: `src/aimoon/screener.py`
- Modify: `src/aimoon/scoring/__init__.py`

- [ ] **Step 1: Update models.py**

Remove the lazy import inside `total_score` property AND the `suggestion` property's lazy import. Make `total_score` a plain field:

```python
@dataclass(frozen=True)
class ScoredStock:
    code: str
    name: str
    price: float
    pct_change: float = 0.0
    turnover: float | None = None
    pe: float | None = None
    pb: float | None = None
    market_cap_yi: float | None = None
    signals: tuple[Signal, ...] = ()
    rps: dict[str, float] = field(default_factory=dict)
    ml_score: int | None = None
    hybrid_score: int | None = None
    total_score: int = 0  # Computed by scoring layer
```

Remove the `total_score` property, the `suggestion` property, and the `_cached_total_score` field/hack entirely. The `suggestion` logic moves to `scoring/portfolio.py` or `scoring/hybrid_scorer.py` as a standalone function.

- [ ] **Step 2: Update scoring/__init__.py**

In `collect_signals()` and `score_portfolio()`, compute `total_score` before constructing `ScoredStock`:

```python
from aimoon.scoring.hybrid_scorer import compute_hybrid_score

# Before returning ScoredStock:
ts = compute_hybrid_score(signals)
return ScoredStock(..., total_score=ts)
```

Also move the `get_suggestion()` function (previously lazy-imported by `ScoredStock.suggestion`) to `scoring/hybrid_scorer.py` as a public function. Update callers that use `stock.suggestion` to call `get_suggestion(stock)` instead.

- [ ] **Step 3: Update screener.py**

In `screen_stock()`, compute `total_score` before constructing `ScoredStock`.

- [ ] **Step 4: Verify**

```bash
python -c "from aimoon.models import ScoredStock; s = ScoredStock(code='000001', name='test', price=10.0); print(s.total_score)"
ruff check src/aimoon/models.py src/aimoon/screener.py src/aimoon/scoring/__init__.py
```

- [ ] **Step 5: Commit**

```bash
git add src/aimoon/models.py src/aimoon/screener.py src/aimoon/scoring/__init__.py
git commit -m "refactor: fix models.py reverse dependency on scoring/"
```

---

### Task 19: Layer 3 verification

- [ ] **Step 1: Run full lint**

Run: `ruff check src/aimoon`

- [ ] **Step 2: Run type check**

Run: `mypy src/aimoon --ignore-missing-imports`

- [ ] **Step 3: Run demo**

Run: `aimoon --demo`

- [ ] **Step 4: Commit if needed**

---

## Layer 4: Integration — Boundaries & Cleanup

### Task 20: Split scoring/__init__.py

**Files:**
- Create: `src/aimoon/scoring/portfolio.py`
- Modify: `src/aimoon/scoring/__init__.py`

- [ ] **Step 1: Create scoring/portfolio.py**

Move `score_portfolio()` and `hybrid_score()` from `scoring/__init__.py` to `scoring/portfolio.py`.

- [ ] **Step 2: Slim down scoring/__init__.py**

Keep only `__all__` exports and `collect_signals()`.

- [ ] **Step 3: Add re-exports in scoring/__init__.py**

```python
from aimoon.scoring.portfolio import score_portfolio, hybrid_score
```

- [ ] **Step 4: Verify and commit**

```bash
ruff check src/aimoon/scoring/
python -c "from aimoon.scoring import collect_signals, score_portfolio, hybrid_score"
git add src/aimoon/scoring/
git commit -m "refactor: split scoring/__init__.py, extract portfolio.py"
```

---

### Task 21: Optimize screener.py panel builds

**Files:**
- Modify: `src/aimoon/screener.py`

- [ ] **Step 1: Read screener.py and find the two panel builds**

`_inject_alpha_signals()` and `_inject_ml_signals()` each call `build_panel(all_klines)`.

- [ ] **Step 2: Refactor to build panel once**

```python
# In screen_universe(), after collecting all_klines:
panel = build_panel(all_klines)
add_all_indicators_batch(panel)

# Pass panel to both inject functions:
_inject_alpha_signals(results, panel)  # instead of all_klines
_inject_ml_signals(results, panel)     # instead of all_klines
```

Update `_inject_alpha_signals` and `_inject_ml_signals` signatures to accept `panel` directly instead of `all_klines`.

- [ ] **Step 3: Verify and commit**

```bash
ruff check src/aimoon/screener.py
aimoon --demo
git add src/aimoon/screener.py
git commit -m "perf: build panel once in screener, avoid duplicate computation"
```

---

### Task 22: Clean cli.py lazy imports

**Files:**
- Modify: `src/aimoon/cli.py`

- [ ] **Step 1: Read cli.py and identify scattered lazy imports**

Find all `from aimoon import ...` inside function bodies.

- [ ] **Step 2: Group imports at function entry points**

For each function that has scattered lazy imports, move them all to the top of the function body:

```python
def _run_backtest(args, cfg, fmt):
    from aimoon.enhanced_backtest import EnhancedBacktestEngine
    from aimoon.scoring.rps import compute_rps
    from aimoon.risk import RiskLimits, check_risk_limits
    from aimoon.scoring.turtle import generate_turtle_plan
    # ... rest of function
```

- [ ] **Step 3: Verify and commit**

```bash
ruff check src/aimoon/cli.py
aimoon --demo
git add src/aimoon/cli.py
git commit -m "refactor: group cli.py lazy imports at function entry points"
```

---

### Task 23: Delete remaining dead code

**Files:**
- Modify: `src/aimoon/factors/scorer.py`
- Modify: `src/aimoon/metrics.py`

- [ ] **Step 1: Remove unused import in factors/scorer.py**

```python
# Before (line 15):
from aimoon.factors.quality import _greedy_correlation_filter  # noqa: F401

# After: delete this line entirely
```

- [ ] **Step 2: Check metrics.py self-referential import**

Read `metrics.py` lines 20+ to find the self-referential import. If it exists, remove it.

- [ ] **Step 3: Verify and commit**

```bash
ruff check src/aimoon/factors/scorer.py src/aimoon/metrics.py
git add src/aimoon/factors/scorer.py src/aimoon/metrics.py
git commit -m "chore: remove dead imports in scorer.py and metrics.py"
```

---

### Task 24: Final verification

- [ ] **Step 1: Run full lint**

Run: `ruff check src/aimoon`
Expected: 0 errors (or only pre-existing)

- [ ] **Step 2: Run type check**

Run: `mypy src/aimoon --ignore-missing-imports`

- [ ] **Step 3: Run formatting check**

Run: `black --check src/aimoon`

- [ ] **Step 4: Run demo pipeline**

Run: `aimoon --demo`
Expected: Complete without error

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "refactor: complete architecture cleanup — unified cache, security fix, deleted duplicates, split god files"
```

---

## Summary

| Task | Description | Files Modified | Files Created | Files Deleted |
|------|-------------|---------------|---------------|---------------|
| 1 | Delete DataManager | 1 | 0 | 1 |
| 2-5 | Parameterize cache paths | 7 | 0 | 0 |
| 6 | Layer 1 verification | 0 | 0 | 0 |
| 7 | Fix pickle → JSON | 1 | 0 | 0 |
| 8 | Export private members | 2 | 0 | 0 |
| 9 | Split filters.py | 1 | 2 | 0 |
| 10 | Move fix_kline_dates | 3 | 0 | 0 |
| 11 | Layer 2 verification | 0 | 0 | 0 |
| 12 | Migrate risk_controls | 0 | 2 | 0 |
| 13 | Update enhanced_backtest imports | 5 | 0 | 0 |
| 14 | Update output/cli callers | 2 | 0 | 0 |
| 15 | Delete old modules | 0 | 0 | 4 |
| 16 | Split trainer.py | 1 | 3 | 0 |
| 17 | Split ensemble.py | 1 | 2 | 0 |
| 18 | Fix models reverse dep | 3 | 0 | 0 |
| 19 | Layer 3 verification | 0 | 0 | 0 |
| 20 | Split scoring/__init__ | 1 | 1 | 0 |
| 21 | Optimize screener panel | 1 | 0 | 0 |
| 22 | Clean cli.py imports | 1 | 0 | 0 |
| 23 | Delete dead code | 2 | 0 | 0 |
| 24 | Final verification | 0 | 0 | 0 |
| **Total** | | **32** | **10** | **5** |
