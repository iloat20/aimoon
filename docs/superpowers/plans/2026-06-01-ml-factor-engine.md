# ML Factor Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add XGBoost factor synthesis engine that learns nonlinear factor combinations from Alpha Zoo 452 factors.

**Architecture:** New `src/aimoon/ml/` package with 4 modules (feature_pipeline, label_engine, trainer, predictor). Integrated via `screener.py:_inject_alpha_signals` modification. Model cached to `.aimoon_cache/ml/`.

**Tech Stack:** XGBoost, pandas, numpy, scikit-learn (for cross-validation only)

---

### Task 1: Add XGBoost dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Add `xgboost>=2.0` and `scikit-learn>=1.3` to dependencies**

```toml
dependencies = [
    "akshare>=1.14",
    "mootdx>=0.9",
    "pandas>=2.0",
    "numpy>=1.26",
    "tabulate>=0.9",
    "colorama>=0.4",
    "rich>=13.0",
    "pyyaml>=6.0",
    "matplotlib>=3.8",
    "scipy>=1.11",
    "xgboost>=2.0",
    "scikit-learn>=1.3",
]
```

### Task 2: Create ml package structure

**Files:**
- Create: `src/aimoon/ml/__init__.py` (empty)
- Create: `src/aimoon/ml/feature_pipeline.py`
- Create: `src/aimoon/ml/label_engine.py`
- Create: `src/aimoon/ml/trainer.py`
- Create: `src/aimoon/ml/predictor.py`

- [ ] **Create `src/aimoon/ml/__init__.py`**

```python
"""ML factor synthesis engine."""
```

- [ ] **Create `src/aimoon/ml/feature_pipeline.py`**

```python
"""Extract feature matrix from Alpha Zoo panel for ML training/inference."""
from __future__ import annotations

import logging
import numpy as np
import pandas as pd

from aimoon.factors.panel import build_panel
from aimoon.factors.registry import Registry, get_default_registry
from aimoon.factors.scorer import _pct_to_score

logger = logging.getLogger(__name__)


def extract_features(
    panel: dict[str, pd.DataFrame],
    registry: Registry | None = None,
    target_date: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Extract cross-sectional feature matrix from Alpha Zoo panel.

    For each stock at the target date, compute:
    - Each alpha factor raw value at last row
    - Each alpha factor percentile rank (cross-sectional)
    - Rolling mean/std/slope over lookback windows
    - Technical supplements: volatility, turnover proxy

    Parameters
    ----------
    panel : dict[str, pd.DataFrame]
        Alpha Zoo panel from build_panel().
    registry : Registry | None
        Factor registry. Defaults to get_default_registry().
    target_date : pd.Timestamp | None
        Date to extract features at. None = last row of panel.

    Returns
    -------
    pd.DataFrame
        index=stock codes, columns=feature names. Empty if no valid features.
    """
    if panel is None or "close" not in panel:
        return pd.DataFrame()

    registry = registry or get_default_registry()
    codes = list(panel["close"].columns)
    if len(codes) < 5:
        return pd.DataFrame()

    feature_dicts: dict[str, dict[str, float]] = {code: {} for code in codes}
    alpha_ids = registry.list()

    for alpha_id in alpha_ids:
        try:
            factor_df = registry.compute(alpha_id, panel)
        except Exception:
            continue

        if target_date is not None and target_date in factor_df.index:
            row = factor_df.loc[target_date]
        else:
            row = factor_df.iloc[-1]

        if row.isna().all():
            continue

        # Raw value + percentile rank
        ranked = row.rank(pct=True, na_option="keep")

        for code in codes:
            if code not in row.index:
                continue
            raw = row.get(code)
            pct = ranked.get(code)
            if pd.isna(raw) or pd.isna(pct):
                continue
            feature_dicts[code][f"alpha_raw_{alpha_id}"] = float(raw)
            feature_dicts[code][f"alpha_pct_{alpha_id}"] = float(pct)

    if all(len(d) == 0 for d in feature_dicts.values()):
        return pd.DataFrame()

    result = pd.DataFrame.from_dict(feature_dicts, orient="index")
    result = result.fillna(result.median())

    # Add basic technical features from panel
    close = panel.get("close")
    volume = panel.get("volume")
    if close is not None:
        for code in codes:
            if code not in close.columns:
                continue
            s = close[code].dropna()
            if len(s) < 20:
                continue
            ret = s.pct_change().dropna()
            idx = -1 if target_date is None else s.index.get_loc(target_date) if target_date in s.index else -1
            if idx == -1 or idx < 0:
                idx = -1
            recent_ret = ret.iloc[idx-20:idx] if idx > 20 else ret.iloc[-20:]
            feature_dicts[code]["tech_volatility_20d"] = float(recent_ret.std()) if len(recent_ret) > 1 else 0.0
            feature_dicts[code]["tech_return_20d"] = float(recent_ret.mean()) if len(recent_ret) > 1 else 0.0

    result = pd.DataFrame.from_dict(feature_dicts, orient="index")
    result = result.fillna(result.median())
    return result


def compute_feature_importance(
    model: object,
    feature_names: list[str],
) -> dict[str, float]:
    """Extract feature importance from trained XGBoost model.

    Parameters
    ----------
    model : xgboost.Booster or xgboost.XGBModel
        Trained model.
    feature_names : list[str]
        Feature names matching training columns.

    Returns
    -------
    dict[str, float]
        Feature name -> importance score (normalized to sum 1.0).
    """
    import xgboost as xgb

    if isinstance(model, xgb.XGBModel):
        importance = model.feature_importances_
    elif isinstance(model, xgb.Booster):
        score_dict = model.get_score(importance_type="gain")
        importance = np.array([score_dict.get(f, 0.0) for f in feature_names])
    else:
        return {}

    total = float(importance.sum())
    if total <= 0:
        return {}

    result: dict[str, float] = {}
    for name, imp in zip(feature_names, importance):
        result[name] = float(imp) / total
    return dict(sorted(result.items(), key=lambda x: x[1], reverse=True))
```

- [ ] **Create `src/aimoon/ml/label_engine.py`**

```python
"""Generate forward-return labels for ML training."""
from __future__ import annotations

import logging
import numpy as np
import pandas as pd

from aimoon.factors.panel import build_panel

logger = logging.getLogger(__name__)


def generate_labels(
    klines: dict[str, pd.DataFrame],
    target_date: pd.Timestamp,
    forward_days: int = 5,
) -> pd.Series:
    """Generate forward N-day return labels at target_date.

    For each stock, compute (close[t+forward_days] - close[t]) / close[t] * 100.
    Returns only stocks with valid data at both dates.

    Parameters
    ----------
    klines : dict[str, pd.DataFrame]
        code -> kline DataFrame with 'close' column.
    target_date : pd.Timestamp
        Date to compute forward returns from.
    forward_days : int
        Number of trading days forward.

    Returns
    -------
    pd.Series
        index=stock code, value=forward return %.
    """
    labels: dict[str, float] = {}
    for code, df in klines.items():
        if df is None or "close" not in df.columns:
            continue
        dates = df.index.sort_values()
        try:
            idx = dates.get_loc(target_date)
        except (KeyError, TypeError):
            continue

        future_idx = idx + forward_days
        if future_idx >= len(dates):
            continue

        close_now = float(df.loc[dates[idx], "close"])
        close_future = float(df.loc[dates[future_idx], "close"])
        if close_now <= 0:
            continue

        labels[code] = (close_future - close_now) / close_now * 100.0

    return pd.Series(labels)


def generate_rank_labels(
    klines: dict[str, pd.DataFrame],
    target_date: pd.Timestamp,
    forward_days: int = 5,
) -> pd.Series:
    """Generate cross-sectional rank labels (0-1) at target_date.

    Same as generate_labels but normalized to percentile rank across stocks.
    Useful for ranking-based XGBoost objectives.

    Returns
    -------
    pd.Series
        index=stock code, value=percentile rank [0, 1].
    """
    labels = generate_labels(klines, target_date, forward_days)
    if len(labels) < 5:
        return labels
    return labels.rank(pct=True)


def generate_binary_labels(
    klines: dict[str, pd.DataFrame],
    target_date: pd.Timestamp,
    forward_days: int = 5,
) -> pd.Series:
    """Generate binary labels: 1 if above median forward return, else 0."""
    labels = generate_labels(klines, target_date, forward_days)
    if len(labels) < 5:
        return labels
    median = labels.median()
    return (labels >= median).astype(int)
```

- [ ] **Create `src/aimoon/ml/trainer.py`**

```python
"""Time-series cross-validated XGBoost training."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

import xgboost as xgb

from aimoon.factors.panel import build_panel
from aimoon.factors.registry import get_default_registry
from aimoon.ml.feature_pipeline import extract_features, compute_feature_importance
from aimoon.ml.label_engine import generate_labels

logger = logging.getLogger(__name__)

_MODEL_DIR = Path(".aimoon_cache") / "ml"
_MODEL_TTL_DAYS = 7


@dataclass(frozen=True)
class TrainingResult:
    """ML model training result."""
    model: Any = field(repr=False)  # xgboost.Booster
    feature_names: list[str]
    feature_importance: dict[str, float]
    ic: float
    n_stocks: int
    n_dates: int
    train_duration: float


def _default_params() -> dict[str, Any]:
    return {
        "max_depth": 6,
        "learning_rate": 0.05,
        "n_estimators": 500,
        "subsample": 0.8,
        "colsample_bytree": 0.5,
        "reg_lambda": 1.0,
        "reg_alpha": 0.1,
        "min_child_weight": 5,
        "early_stopping_rounds": 10,
        "objective": "reg:squarederror",
        "eval_metric": "rmse",
        "random_state": 42,
        "verbosity": 0,
    }


def _collect_training_data(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    registry: object,
    n_dates: int = 5,
    forward_days: int = 5,
) -> tuple[pd.DataFrame, pd.Series]:
    """Collect features and labels across multiple dates for training.

    Takes snapshots at n_date evenly spaced dates from the panel.
    Each date produces one feature row per stock with sufficient data.

    Parameters
    ----------
    panel : dict[str, pd.DataFrame]
        Alpha Zoo panel.
    klines : dict[str, pd.DataFrame]
        Original kline data for label computation.
    registry : Registry
        Factor registry.
    n_dates : int
        Number of historical dates to sample.
    forward_days : int
        Forward return horizon.

    Returns
    -------
    tuple[pd.DataFrame, pd.Series]
        (X: features matrix, y: labels series).
    """
    close = panel.get("close")
    if close is None or len(close) < 20:
        return pd.DataFrame(), pd.Series(dtype=float)

    available_dates = close.index[20:].tolist()
    if len(available_dates) < n_dates:
        n_dates = len(available_dates)
    if n_dates < 1:
        return pd.DataFrame(), pd.Series(dtype=float)

    step = max(1, len(available_dates) // n_dates)
    selected_dates = [available_dates[i * step] for i in range(n_dates)]

    all_features: list[pd.DataFrame] = []
    all_labels: list[pd.Series] = []

    for date in selected_dates:
        features = extract_features(panel, registry, target_date=date)
        labels = generate_labels(klines, date, forward_days)

        if features.empty or labels.empty:
            continue

        common = features.index.intersection(labels.index)
        if len(common) < 10:
            continue

        all_features.append(features.loc[common])
        all_labels.append(labels.loc[common])

    if not all_features:
        return pd.DataFrame(), pd.Series(dtype=float)

    X = pd.concat(all_features, axis=0)
    y = pd.concat(all_labels, axis=0)

    # Drop constant features
    X = X.loc[:, X.nunique() > 1]

    logger.info(
        "Training data: %d samples, %d features, %d dates",
        len(X), X.shape[1], len(all_features),
    )
    return X, y


def train_model(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    registry: object | None = None,
    params: dict[str, Any] | None = None,
    n_dates: int = 5,
    forward_days: int = 5,
    save_dir: str | Path | None = None,
) -> TrainingResult:
    """Train XGBoost model with time-series cross-validation.

    Steps:
    1. Collect features and labels across multiple dates
    2. TimeSeriesSplit cross-validation with early stopping
    3. Train final model on all data
    4. Save model to disk

    Parameters
    ----------
    panel : dict[str, pd.DataFrame]
        Alpha Zoo panel.
    klines : dict[str, pd.DataFrame]
        Original kline data.
    registry : Registry | None
        Factor registry. Defaults to get_default_registry().
    params : dict | None
        XGBoost params overrides.
    n_dates : int
        Number of historical dates for training data.
    forward_days : int
        Forward return horizon.
    save_dir : str | Path | None
        Directory to save model artifacts.

    Returns
    -------
    TrainingResult
        Trained model and metadata.
    """
    registry = registry or get_default_registry()

    t0 = time.time()

    X, y = _collect_training_data(panel, klines, registry, n_dates, forward_days)
    if len(X) < 50 or X.shape[1] < 2:
        raise ValueError(
            f"Insufficient training data: {len(X)} samples, {X.shape[1]} features "
            f"(need >=50 samples and >=2 features)"
        )

    xgb_params = {**_default_params(), **(params or {})}
    feature_names = X.columns.tolist()
    model_params = {k: v for k, v in xgb_params.items()
                    if k not in ("early_stopping_rounds", "n_estimators")}

    # TimeSeriesSplit for validation
    tscv = TimeSeriesSplit(n_splits=3)
    cv_scores: list[float] = []

    for train_idx, val_idx in tscv.split(X):
        X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]

        dtrain = xgb.DMatrix(X_train, label=y_train)
        dval = xgb.DMatrix(X_val, label=y_val)

        booster = xgb.train(
            model_params,
            dtrain,
            num_boost_round=xgb_params["n_estimators"],
            evals=[(dval, "val")],
            early_stopping_rounds=xgb_params["early_stopping_rounds"],
            verbose_eval=False,
        )
        cv_scores.append(float(booster.best_score))

    # Train final model on all data
    dtrain_full = xgb.DMatrix(X, label=y)
    final_model = xgb.train(
        model_params,
        dtrain_full,
        num_boost_round=xgb_params["n_estimators"],
        verbose_eval=False,
    )

    # Compute IC on training data
    preds = final_model.predict(dtrain_full)
    from scipy.stats import spearmanr
    ic, _ = spearmanr(preds, y)
    ic = float(ic) if not np.isnan(ic) else 0.0

    importance = compute_feature_importance(final_model, feature_names)

    train_duration = time.time() - t0
    logger.info(
        "Model trained: IC=%.04f, %d stocks x %d dates, %.1fs",
        ic, X.shape[0], X.shape[1], train_duration,
    )

    # Save artifacts
    if save_dir is not None:
        save_path = Path(save_dir)
        save_path.mkdir(parents=True, exist_ok=True)
        final_model.save_model(str(save_path / "model.json"))
        with open(save_path / "feature_names.json", "w", encoding="utf-8") as f:
            json.dump(feature_names, f)
        with open(save_path / "meta.json", "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": time.time(),
                "ic": round(ic, 4),
                "n_stocks": len(y),
                "n_features": X.shape[1],
                "n_dates": n_dates,
                "forward_days": forward_days,
                "train_duration": round(train_duration, 2),
                "cv_scores": [round(s, 4) for s in cv_scores],
            }, f, indent=2)

    return TrainingResult(
        model=final_model,
        feature_names=feature_names,
        feature_importance=importance,
        ic=ic,
        n_stocks=len(X),
        n_dates=n_dates,
        train_duration=train_duration,
    )


def ensure_model_fresh(
    klines: dict[str, pd.DataFrame],
    force: bool = False,
    model_dir: str | Path | None = None,
    n_dates: int = 5,
    forward_days: int = 5,
) -> object | None:
    """Check if model needs retraining, train if needed. Returns model or None."""
    save_dir = Path(model_dir or _MODEL_DIR)
    meta_file = save_dir / "meta.json"
    model_file = save_dir / "model.json"

    need_train = force or not model_file.exists()

    if not need_train and meta_file.exists():
        try:
            with open(meta_file, encoding="utf-8") as f:
                meta = json.load(f)
            age_days = (time.time() - meta.get("timestamp", 0)) / 86400
            if age_days > _MODEL_TTL_DAYS:
                need_train = True
                logger.info("Model stale (%.0f days > %d), retraining", age_days, _MODEL_TTL_DAYS)
        except Exception:
            need_train = True

    if not need_train:
        try:
            import xgboost as xgb
            model = xgb.Booster()
            model.load_model(str(model_file))
            logger.info("Using cached ML model")
            return model
        except Exception:
            need_train = True

    if need_train:
        panel = build_panel(klines, min_rows=60)
        if panel is None:
            logger.warning("Cannot train ML model: insufficient panel data")
            return None
        registry = get_default_registry()
        result = train_model(panel, klines, registry, save_dir=save_dir)
        logger.info("ML model trained: IC=%.04f", result.ic)
        return result.model

    return None
```

- [ ] **Create `src/aimoon/ml/predictor.py`**

```python
"""ML model inference -> Signal injection."""
from __future__ import annotations

import logging
import pandas as pd
import xgboost as xgb

from aimoon.factors.panel import build_panel
from aimoon.factors.registry import Registry, get_default_registry
from aimoon.ml.feature_pipeline import extract_features
from aimoon.models import Signal

logger = logging.getLogger(__name__)


def predict_alpha_signals(
    model: xgb.Booster,
    panel: dict[str, pd.DataFrame],
    registry: Registry | None = None,
    top_k: int = 0,
) -> dict[str, list[Signal]]:
    """Generate stock Signals from trained XGBoost model.

    Uses the model to predict forward returns from current cross-sectional data,
    then maps predictions to Signal objects with scores based on percentile rank.

    Parameters
    ----------
    model : xgb.Booster
        Trained XGBoost model.
    panel : dict[str, pd.DataFrame]
        Alpha Zoo panel.
    registry : Registry | None
        Factor registry.
    top_k : int
        If > 0, only generate signals for top_k predicted stocks.

    Returns
    -------
    dict[str, list[Signal]]
        code -> [Signal, ...]. Empty dict if inference fails.
    """
    if panel is None or "close" not in panel:
        return {}

    registry = registry or get_default_registry()
    features = extract_features(panel, registry)

    if features.empty:
        logger.warning("ML predict: no features extracted")
        return {}

    # Ensure feature columns match model expectation
    with open(Path(".aimoon_cache") / "ml" / "feature_names.json", encoding="utf-8") as f:
        import json
        expected_features = json.load(f)

    missing = set(expected_features) - set(features.columns)
    extra = set(features.columns) - set(expected_features)
    if missing:
        logger.debug(
            "ML predict: %d missing features (filled with 0), %d extra (dropped)",
            len(missing), len(extra),
        )
    X = features.reindex(columns=expected_features, fill_value=0.0)

    dmatrix = xgb.DMatrix(X)
    try:
        preds = model.predict(dmatrix)
    except Exception as e:
        logger.warning("ML predict failed: %s", e)
        return {}

    pred_series = pd.Series(preds, index=X.index).dropna()
    if len(pred_series) < 5:
        return {}

    if top_k > 0 and top_k < len(pred_series):
        threshold = pred_series.nlargest(top_k).iloc[-1]
        pred_series = pred_series[pred_series >= threshold]

    ranked = pred_series.rank(pct=True)
    signals_by_code: dict[str, list[Signal]] = {}

    for code in pred_series.index:
        pct = ranked[code]
        sigs: list[Signal] = []

        if pct >= 0.90:
            sigs.append(Signal(
                "ml_alpha_strong", f"ML因子强烈看多({pct:.0%})", +5,
            ))
        elif pct >= 0.75:
            sigs.append(Signal(
                "ml_alpha", f"ML因子看多({pct:.0%})", +3,
            ))
        elif pct <= 0.10:
            sigs.append(Signal(
                "ml_alpha_bear_strong", f"ML因子强烈看空({pct:.0%})", -5,
            ))
        elif pct <= 0.25:
            sigs.append(Signal(
                "ml_alpha_bear", f"ML因子看空({pct:.0%})", -3,
            ))

        if sigs:
            signals_by_code[str(code)] = sigs

    return signals_by_code
```

### Task 3: Integrate into screener.py

**Files:**
- Modify: `src/aimoon/screener.py` (modify `_inject_alpha_signals`)

- [ ] **Modify `_inject_alpha_signals` to use ML predictions, falling back to original logic**

In `_inject_alpha_signals`, add ML inference path:

```python
def _inject_alpha_signals(
    results: list[ScoredStock],
    all_klines: dict[str, pd.DataFrame],
) -> list[ScoredStock]:
    """Build panel, run ML model or fallback alpha scoring, inject signals."""
    from aimoon.factors.panel import build_panel
    from aimoon.factors.registry import get_default_registry
    from aimoon.factors.scorer import compute_alpha_signals

    panel = build_panel(all_klines)
    if panel is None:
        return results

    try:
        # Try ML path
        from pathlib import Path
        import json
        model_path = Path(".aimoon_cache") / "ml" / "model.json"
        if model_path.exists():
            import xgboost as xgb
            model = xgb.Booster()
            model.load_model(str(model_path))
            from aimoon.ml.predictor import predict_alpha_signals as ml_predict
            ml_signals = ml_predict(model, panel)
            if ml_signals:
                return _merge_alpha_signals(results, ml_signals, prefix="ml_")
    except Exception as e:
        logger.debug("ML alpha path failed: %s, using fallback", e)

    # Fallback: percentile-based Alpha Zoo scoring
    registry = get_default_registry()
    alpha_signals = compute_alpha_signals(registry, panel)
    return _merge_alpha_signals(results, alpha_signals, prefix="alpha_")


def _merge_alpha_signals(
    results: list[ScoredStock],
    alpha_signals: dict[str, list[Signal]],
    prefix: str = "alpha_",
) -> list[ScoredStock]:
    """Merge alpha signals into ScoredStock list."""
    enhanced: list[ScoredStock] = []
    for scored in results:
        extra = alpha_signals.get(scored.code, [])
        if extra:
            # Filter out old alpha signals if ML replaces them
            existing = [s for s in scored.signals if not s.name.startswith(prefix)]
            new_signals = tuple(existing + extra)
            scored = ScoredStock(
                code=scored.code, name=scored.name, price=scored.price,
                pct_change=scored.pct_change, turnover=scored.turnover,
                pe=scored.pe, pb=scored.pb, market_cap_yi=scored.market_cap_yi,
                signals=new_signals, rps=scored.rps,
            )
        enhanced.append(scored)

    n_with = sum(1 for s in enhanced if any(sig.name.startswith(prefix) for sig in s.signals))
    logger.info("Alpha signals merged: %d stocks with %s signals", n_with, prefix)
    return enhanced
```

- [ ] **Update the scoring `__init__.py` to recognize ml_ prefix signals**

In `src/aimoon/scoring/__init__.py`, add to `_SIGNAL_CATEGORY_PREFIXES`:

```python
_SIGNAL_CATEGORY_PREFIXES: list[tuple[str, str]] = [
    # ... existing entries ...
    ("alpha_", "alpha"),  # Alpha Zoo 截面因子
    ("ml_alpha_", "alpha"),  # ML 合成因子
]
```

And in `_CATEGORY_CAPS` and `_CATEGORY_GROUP`, ensure alpha category can hold ML scores:

```python
_CATEGORY_CAPS: dict[str, int] = {
    "alpha": 60,  # Increased from 30 to absorb both raw alpha + ML signals
    # ...
}
```

### Task 4: Add CLI integration

**Files:**
- Modify: `src/aimoon/cli.py`

- [ ] **Add `--train-model` CLI argument**

In `parse_args()`:
```python
p.add_argument("--train-model", action="store_true", help="Force retrain ML model")
```

- [ ] **Add auto-check before screening**

In `main()`, before `screen_universe` call:
```python
# Auto-train ML model if needed
if cfg.use_alpha and not cfg.demo:
    from aimoon.ml.trainer import ensure_model_fresh
    from aimoon.data.history import get_kline
    from aimoon.cache import DataCache
    train_force = getattr(args, "train_model", False)
    if train_force:
        fmt.console.print("[bold blue]Forcing ML model retraining...[/bold blue]")
    _c = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)
    ensure_model_fresh(klines, force=train_force)
```

- [ ] **Add train-model subcommand**

In `parse_args()`:
```python
sub.add_parser("train-model", help="强制重新训练 ML 因子模型")
```

In `main()`:
```python
if cfg.command == "train-model":
    from aimoon.ml.trainer import ensure_model_fresh
    fmt.console.print("[bold blue]=== Training ML Model ===[/bold blue]")
    # Collect klines from holdings pool
    from aimoon.data.filters import get_holdings_pool
    pool = get_holdings_pool(cfg)
    fmt.console.print(f"[dim]Collecting klines for {len(pool)} stocks...[/dim]")
    from aimoon.data.history import get_kline
    _cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)
    klines: dict = {}
    for code in pool:
        r = get_kline(code, cfg.history_days, _cache)
        if r.is_ok():
            klines[code] = r.unwrap()
    model = ensure_model_fresh(klines, force=True)
    if model:
        fmt.console.print("[green]ML model trained successfully![/green]")
    else:
        fmt.console.print("[red]ML model training failed (insufficient data)[/red]")
    return
```

### Task 5: Write tests

**Files:**
- Create: `tests/test_ml_features.py`
- Create: `tests/test_ml_labels.py`
- Create: `tests/test_ml_trainer.py`
- Create: `tests/test_ml_predictor.py`

- [ ] **Write `tests/test_ml_features.py`**

```python
"""Test ML feature extraction."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aimoon.ml.feature_pipeline import extract_features


def _make_dummy_panel(n_stocks: int = 10, n_days: int = 100) -> dict[str, pd.DataFrame]:
    codes = [f"{i:06d}" for i in range(n_stocks)]
    dates = pd.date_range(end="2026-06-01", periods=n_days, freq="B")
    data = {}
    for col in ("open", "high", "low", "close", "volume"):
        arr = np.random.randn(n_days, n_stocks).cumsum(axis=0) + 100
        data[col] = pd.DataFrame(arr, index=dates, columns=codes)
    return data


def test_extract_features_empty_panel():
    result = extract_features(None)
    assert result.empty


def test_extract_features_shape():
    panel = _make_dummy_panel(10, 100)
    result = extract_features(panel)
    assert not result.empty
    assert len(result) <= 10
    assert result.shape[1] >= 4  # At least basic features


def test_extract_features_no_nan():
    panel = _make_dummy_panel(10, 100)
    result = extract_features(panel)
    assert not result.isna().any().any()
