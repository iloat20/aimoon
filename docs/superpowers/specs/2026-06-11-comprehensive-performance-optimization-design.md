# Comprehensive Performance Optimization Design

## Overview

Optimize the aimoon quantitative trading system across all performance dimensions: factor computation speed, backtest runtime, data caching efficiency, ML prediction speed, and quick wins.

**Current State:**
- Backtest runtime: ~5s portfolio simulation (optimized from 20s)
- First-run screening: ~80s (dominated by data fetching + factor computation)
- Factor quality filtering: Very expensive on first run (457 factors × ICIR + turnover + correlation)

**Target State:**
- Backtest runtime: <2s portfolio simulation
- First-run screening: <40s
- Factor quality filtering: 10x speedup

## Optimization Areas

### 1. Factor Computation Speed

#### Vectorize Factor Correlation (`scorer.py`)

**Current:** O(N²) nested loops with Spearman correlation per factor pair
```python
# Lines 501-516: ~104K calls for 457 factors
for i, f1 in enumerate(factors):
    for j, f2 in enumerate(factors):
        if i < j:
            corr = spearmanr(data[f1], data[f2])
```

**Proposed:** Single `pd.DataFrame.corr(method='spearman')` call
```python
# Single vectorized call
corr_matrix = factor_df.corr(method='spearman')
```

**Expected:** 457 factors correlation from ~30s → <1s

#### Vectorize Factor Turnover (`scorer.py`)

**Current:** Per-factor iteration across all dates
```python
# Lines 368-398: iterate all factors × all dates
for factor in factors:
    for date in dates:
        quintile = pd.qcut(ranks[date], 5, labels=False)
        turnover[factor] += (quintile.diff() != 0).sum()
```

**Proposed:** Panel-level vectorized operations
```python
# Compute rank quintiles for all factors at once
ranks = factor_panel.rank(axis=1, pct=True)
quintiles = (ranks * 5).astype(int)
turnover = quintiles.diff().abs().sum(axis=0)
```

**Expected:** 10x speedup

#### Batch Factor Computation (`performance.py`)

**Current:** `ThreadPoolExecutor` (limited by GIL for CPU-bound tasks)

**Proposed:**
- Use `ProcessPoolExecutor` on Linux/Mac (bypass GIL)
- On Windows, use `concurrent.futures.ProcessPoolExecutor` with `spawn` context
- Keep `ThreadPoolExecutor` for I/O-bound tasks only

**Expected:** 2-4x speedup on multi-core systems

### 2. Backtest Runtime

#### Vectorize Per-Bar Tech Features (`engine.py`)

**Current:** Python for-loop over stocks
```python
# Lines 612-633: ~12ms per bar
for code in codes:
    close = panel["close"][code].dropna()
    pct = close.pct_change()
    std = close.rolling(20).std()
    mean = close.rolling(20).mean()
```

**Proposed:** Vectorized pandas operations on close panel
```python
# Single vectorized call
close_panel = panel["close"]
pct = close_panel.pct_change()
std = close_panel.rolling(20).std()
mean = close_panel.rolling(20).mean()
```

**Expected:** ~12ms → ~2ms per bar

#### Precompute Regime Detection (`engine.py`)

**Current:** `_detect_regime_safe()` called per bar (~200 calls)

**Proposed:** Cache regime state per date during initialization
```python
# Precompute once
regime_cache = {date: _detect_regime_safe(benchmark, date) for date in dates}
# Lookup during backtest
current_regime = regime_cache.get(bar_date, "sideways")
```

**Expected:** ~200ms → <1ms per bar

#### Batch ML Predictions (`engine.py`)

**Current:** `_get_ml_scores_for_date()` called per bar

**Proposed:** Add `predict_batch()` method to `EnsemblePredictor` and pre-compute predictions for all backtest dates
```python
# New method in EnsemblePredictor
def predict_batch(self, feature_dict: dict[str, pd.DataFrame]) -> dict[str, int]:
    """Predict scores for multiple dates in a single batch call."""
    ...

# Pre-compute once
ml_scores = ensemble.predict_batch({date: extract_features(date) for date in dates})
# Lookup during backtest
current_scores = ml_scores.get(bar_date, {})
```

**Expected:** ~50ms per bar → <1ms per bar

**Note:** Requires implementing `predict_batch()` method in `EnsemblePredictor` class

### 3. Data Caching Efficiency

#### Switch to Parquet (`cache.py`)

**Current:** JSON serialization
```python
# Slow deserialization
df.to_json(path)
df = pd.read_json(path)
```

**Proposed:** Parquet format
```python
# Fast binary serialization
df.to_parquet(path, engine='pyarrow')
df = pd.read_parquet(path, engine='pyarrow')
```

**Expected:** 5-10x faster DataFrame serialization

**Security:** Parquet is not executable (no code injection risk)

**Fallback:** Keep JSON for systems without parquet libraries
```python
try:
    import pyarrow
    engine = 'pyarrow'
except ImportError:
    engine = None  # Fall back to JSON
```

### 4. ML Prediction Speed

#### Batch Feature Extraction (`feature_pipeline.py`)

**Current:** Per-feature Spearman loops
```python
# Lines 581-643: iterate all features
for feature in features:
    corr, pval = spearmanr(feature_values, labels)
```

**Proposed:** Matrix operations
```python
# Single vectorized call
correlations = np.corrcoef(feature_matrix, labels)[0, 1:]
```

**Expected:** 10x speedup

#### Cache Intermediate Results

**Current:** Neutralization design matrix recomputed each run

**Proposed:** Cache X_reg across runs
```python
# Cache OLS design matrix
x_reg_cache = DataCache(cache_dir, ttl_hours=720)
key = f"neutralization_{industry_count}_{size_count}"
x_reg = x_reg_cache.get(key)
if x_reg is None:
    x_reg = compute_design_matrix(industry, size)
    x_reg_cache.set(key, x_reg)
```

### 5. Quick Wins

#### Reduce Logging Overhead

**Current:** Debug-level log statements in hot paths
```python
logger.debug("Processing %s", code)  # Always formats string
```

**Proposed:** Guard with `isEnabledFor()`
```python
if logger.isEnabledFor(logging.DEBUG):
    logger.debug("Processing %s", code)  # Only formats if enabled
```

**Expected:** ~5% speedup in hot paths

#### Minimize Pandas Copies

**Current:** Unnecessary `.copy()` calls
```python
df = data.copy()  # Not always needed
```

**Proposed:** Audit and remove unnecessary copies
- Use `view()` where possible
- Avoid in-place modifications that require copies

#### Pre-allocate Arrays

**Current:** List append in equity curve computation
```python
equity = [100.0]
for bar in bars:
    equity.append(equity[-1] * (1 + return))
```

**Proposed:** Numpy array pre-allocation
```python
equity = np.empty(len(bars) + 1)
equity[0] = 100.0
for i, bar in enumerate(bars):
    equity[i + 1] = equity[i] * (1 + return)
```

**Expected:** ~10% speedup for long backtests

## Implementation Order

1. **Factor computation speed** (highest impact on first-run screening)
   - Vectorize factor correlation
   - Vectorize factor turnover
   - Batch factor computation with ProcessPoolExecutor

2. **Backtest runtime** (highest impact on repeated runs)
   - Vectorize per-bar tech features
   - Precompute regime detection
   - Batch ML predictions

3. **Data caching efficiency** (impact across all operations)
   - Switch to parquet with JSON fallback

4. **ML prediction speed** (impact on screening + backtest)
   - Batch feature extraction
   - Cache intermediate results

5. **Quick wins** (easy gains)
   - Reduce logging overhead
   - Minimize pandas copies
   - Pre-allocate arrays

## Testing Strategy

- **Unit tests**: Add tests for vectorized factor correlation/turnover
- **Integration tests**: Verify backtest results unchanged after optimization
- **Performance benchmarks**: Run before/after each optimization
- **Regression tests**: Ensure all existing functionality preserved

## Risks

1. **Parquet dependency**: May not be available on all systems
   - Mitigation: JSON fallback

2. **ProcessPoolExecutor on Windows**: Spawn context has overhead
   - Mitigation: Keep ThreadPoolExecutor for small factor batches

3. **Vectorization complexity**: May introduce subtle numerical differences
   - Mitigation: Compare results with tolerance (1e-10)

## Success Metrics

- First-run screening: <40s (from ~80s)
- Backtest runtime: <2s portfolio simulation (from ~5s)
- Factor correlation: <1s (from ~30s)
- Factor turnover: 10x speedup
- Memory usage: No increase >20%
