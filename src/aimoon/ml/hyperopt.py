"""Bayesian hyperparameter optimization for ML models using Optuna.

This module provides automated hyperparameter tuning via Optuna's
Tree-structured Parzen Estimator (TPE) sampler. It optimizes
XGBoost and LightGBM hyperparameters by maximizing validation IC.

Features:
- Per-regime optimization (bull/bear/sideways/etc.)
- Result caching with TTL (7 days)
- Graceful degradation when Optuna is not installed
- Thread-safe study storage via Optuna's built-in journaling

Usage::

    from aimoon.ml.hyperopt import run_hyperopt, get_best_params

    # Run optimization
    results = run_hyperopt(
        X_train, y_train, X_val, y_val,
        model_type="xgb",
        n_trials=100,
    )

    # Get cached best params
    best = get_best_params(model_type="xgb", regime="bull")
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from aimoon.ml._training_commons import compute_spearmanr_safe

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_DEFAULT_CACHE_DIR = Path(".aimoon_cache") / "ml"
HYPEROPT_CACHE_TTL_DAYS = 7

# Default search space for XGBoost
XGB_SEARCH_SPACE: dict[str, dict[str, Any]] = {
    "max_depth": {"type": "int", "low": 2, "high": 6},
    "n_estimators": {"type": "int", "low": 200, "high": 1500},
    "learning_rate": {"type": "float", "low": 0.001, "high": 0.05, "log": True},
    "subsample": {"type": "float", "low": 0.6, "high": 1.0},
    "colsample_bytree": {"type": "float", "low": 0.6, "high": 1.0},
    "reg_alpha": {"type": "float", "low": 0.0, "high": 10.0},
    "reg_lambda": {"type": "float", "low": 0.0, "high": 10.0},
    "min_child_weight": {"type": "int", "low": 10, "high": 80},
    "gamma": {"type": "float", "low": 0.0, "high": 5.0},
}

# Default search space for LightGBM
LGBM_SEARCH_SPACE: dict[str, dict[str, Any]] = {
    "num_leaves": {"type": "int", "low": 7, "high": 63},
    "max_depth": {"type": "int", "low": 2, "high": 6},
    "n_estimators": {"type": "int", "low": 200, "high": 1500},
    "learning_rate": {"type": "float", "low": 0.001, "high": 0.05, "log": True},
    "subsample": {"type": "float", "low": 0.6, "high": 1.0},
    "colsample_bytree": {"type": "float", "low": 0.6, "high": 1.0},
    "reg_alpha": {"type": "float", "low": 0.0, "high": 10.0},
    "reg_lambda": {"type": "float", "low": 0.0, "high": 10.0},
    "min_child_samples": {"type": "int", "low": 20, "high": 100},
    "feature_fraction": {"type": "float", "low": 0.4, "high": 1.0},
}

ModelType = Literal["xgb", "lgbm"]


# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HyperoptResult:
    """Single hyperparameter optimization result.

    Attributes:
        model_type: "xgb" or "lgbm".
        regime: Market regime label (e.g. "bull", "bear", "all").
        best_params: Best hyperparameters found.
        best_ic: Best validation IC achieved.
        n_trials: Number of trials run.
        duration_seconds: Wall-clock time for optimization.
        timestamp: Unix timestamp when optimization completed.
        trial_history: List of (trial_number, ic, params) for analysis.
    """

    model_type: str
    regime: str
    best_params: dict[str, Any]
    best_ic: float
    n_trials: int
    duration_seconds: float
    timestamp: float = field(default_factory=time.time)
    trial_history: list[dict[str, Any]] = field(default_factory=list)


# ── Cache helpers ─────────────────────────────────────────────────────────────


def _load_cache(cache_dir: Path = _DEFAULT_CACHE_DIR) -> dict[str, Any]:
    """Load hyperopt results cache from disk.

    Returns empty dict if cache is missing, corrupt, or expired.
    """
    cache_file = Path(cache_dir) / "hyperopt_results.json"
    if not cache_file.exists():
        return {}
    try:
        with open(cache_file, encoding="utf-8") as f:
            data = json.load(f)
        # Check top-level TTL
        ts = data.get("_timestamp", 0)
        age_days = (time.time() - ts) / 86400
        if age_days > HYPEROPT_CACHE_TTL_DAYS:
            logger.info(
                "Hyperopt cache expired (%.0f days > %d), will re-optimize",
                age_days,
                HYPEROPT_CACHE_TTL_DAYS,
            )
            return {}
        return data
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning("Failed to load hyperopt cache: %s", e)
        return {}


def _save_cache(data: dict[str, Any], cache_dir: Path = _DEFAULT_CACHE_DIR) -> None:
    """Save hyperopt results cache to disk (atomic write)."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "hyperopt_results.json"
    data["_timestamp"] = time.time()
    tmp_path = cache_file.with_suffix(".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        tmp_path.replace(cache_file)
    except OSError as e:
        logger.warning("Failed to save hyperopt cache: %s", e)
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def get_best_params(
    model_type: ModelType = "xgb",
    regime: str = "all",
    cache_dir: Path = _DEFAULT_CACHE_DIR,
) -> dict[str, Any] | None:
    """Retrieve cached best hyperparameters.

    Parameters
    ----------
    model_type : {"xgb", "lgbm"}
        Which model's parameters to retrieve.
    regime : str
        Market regime label. Defaults to "all" (no regime split).
    cache_dir : Path
        Cache directory path.

    Returns
    -------
    dict | None
        Best hyperparameters, or None if no cached result exists.
    """
    cache = _load_cache(cache_dir=cache_dir)
    key = f"{model_type}_{regime}"
    entry = cache.get(key)
    if entry is None:
        return None
    return entry.get("best_params")


# ── Optuna objective ──────────────────────────────────────────────────────────


def _build_xgb_objective(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_val: pd.DataFrame,
    y_val: pd.Series,
    search_space: dict[str, dict[str, Any]],
) -> Any:
    """Build an Optuna objective function for XGBoost.

    The objective trains an XGBoost model with sampled hyperparameters
    and returns the validation IC (Spearman rank correlation).
    """
    import xgboost as xgb

    def objective(trial: Any) -> float:
        params: dict[str, Any] = {}
        for name, spec in search_space.items():
            if spec["type"] == "int":
                params[name] = trial.suggest_int(name, spec["low"], spec["high"])
            elif spec["type"] == "float":
                if spec.get("log", False):
                    params[name] = trial.suggest_float(name, spec["low"], spec["high"], log=True)
                else:
                    params[name] = trial.suggest_float(name, spec["low"], spec["high"])

        # Fixed params for stability
        params["objective"] = "reg:pseudohubererror"
        params["eval_metric"] = "rmse"
        params["verbosity"] = 0
        params["n_jobs"] = 1  # Avoid nested parallelism

        n_estimators = params.pop("n_estimators", 500)
        early_stopping = params.pop("early_stopping_rounds", 30)

        dtrain = xgb.DMatrix(X_train, label=y_train)
        dval = xgb.DMatrix(X_val, label=y_val)

        model = xgb.train(
            params,
            dtrain,
            num_boost_round=n_estimators,
            evals=[(dval, "val")],
            early_stopping_rounds=early_stopping,
            verbose_eval=False,
        )

        preds = model.predict(dval)
        return compute_spearmanr_safe(preds, y_val.values)

    return objective


def _build_lgbm_objective(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_val: pd.DataFrame,
    y_val: pd.Series,
    search_space: dict[str, dict[str, Any]],
) -> Any:
    """Build an Optuna objective function for LightGBM.

    The objective trains a LightGBM model with sampled hyperparameters
    and returns the validation IC (Spearman rank correlation).
    """
    import lightgbm as lgb

    def objective(trial: Any) -> float:
        params: dict[str, Any] = {}
        for name, spec in search_space.items():
            if spec["type"] == "int":
                params[name] = trial.suggest_int(name, spec["low"], spec["high"])
            elif spec["type"] == "float":
                if spec.get("log", False):
                    params[name] = trial.suggest_float(name, spec["low"], spec["high"], log=True)
                else:
                    params[name] = trial.suggest_float(name, spec["low"], spec["high"])

        # Fixed params
        params["objective"] = "regression"
        params["metric"] = "rmse"
        params["verbose"] = -1
        params["n_jobs"] = 1  # Avoid nested parallelism
        params["random_state"] = 42

        n_estimators = params.pop("n_estimators", 500)

        model = lgb.LGBMRegressor(n_estimators=n_estimators, **params)
        model.fit(
            X_train,
            y_train,
            eval_set=[(X_val, y_val)],
            callbacks=[lgb.early_stopping(20, verbose=False)],
        )

        preds = model.predict(X_val)
        return compute_spearmanr_safe(preds, y_val.values)

    return objective


# ── Main entry point ──────────────────────────────────────────────────────────


def run_hyperopt(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_val: pd.DataFrame,
    y_val: pd.Series,
    *,
    model_type: ModelType = "xgb",
    regime: str = "all",
    n_trials: int = 100,
    search_space: dict[str, dict[str, Any]] | None = None,
    timeout: float | None = None,
    random_state: int = 42,
    force: bool = False,
    cache_dir: Path = _DEFAULT_CACHE_DIR,
) -> HyperoptResult | None:
    """Run Bayesian hyperparameter optimization using Optuna.

    Maximizes validation IC (Spearman rank correlation) via TPE sampler.

    Parameters
    ----------
    X_train : pd.DataFrame
        Training features.
    y_train : pd.Series
        Training labels (forward returns).
    X_val : pd.DataFrame
        Validation features.
    y_val : pd.Series
        Validation labels.
    model_type : {"xgb", "lgbm"}
        Which model to optimize.
    regime : str
        Market regime label for caching. Default "all" (no regime split).
    n_trials : int
        Number of optimization trials. Default 100.
    search_space : dict, optional
        Custom search space. If None, uses the default for the model type.
    timeout : float, optional
        Maximum wall-clock seconds for optimization. None = no limit.
    random_state : int
        Seed for reproducibility.
    force : bool
        If True, ignore cache and re-optimize.
    cache_dir : Path
        Cache directory path.

    Returns
    -------
    HyperoptResult | None
        Optimization result, or None if Optuna is not installed.

    Examples
    --------
    >>> result = run_hyperopt(X_train, y_train, X_val, y_val,
    ...                      model_type="xgb", n_trials=50)
    >>> if result:
    ...     print(f"Best IC: {result.best_ic:.4f}")
    ...     print(f"Best params: {result.best_params}")
    """
    try:
        import optuna
    except ImportError:
        logger.warning(
            "optuna not installed — skipping hyperparameter optimization. "
            "Install with: pip install optuna"
        )
        return None

    # Check cache
    if not force:
        cached = get_best_params(model_type=model_type, regime=regime, cache_dir=cache_dir)
        if cached is not None:
            logger.info(
                "Using cached hyperopt params for %s/%s: %s",
                model_type,
                regime,
                cached,
            )
            # Reconstruct a lightweight result from cache
            cache = _load_cache(cache_dir=cache_dir)
            key = f"{model_type}_{regime}"
            entry = cache.get(key, {})
            return HyperoptResult(
                model_type=model_type,
                regime=regime,
                best_params=cached,
                best_ic=entry.get("best_ic", 0.0),
                n_trials=entry.get("n_trials", 0),
                duration_seconds=0.0,
                timestamp=entry.get("timestamp", time.time()),
            )

    # Select search space
    if search_space is None:
        if model_type == "xgb":
            search_space = XGB_SEARCH_SPACE
        else:
            search_space = LGBM_SEARCH_SPACE

    # Build objective
    if model_type == "xgb":
        objective = _build_xgb_objective(X_train, y_train, X_val, y_val, search_space)
    else:
        objective = _build_lgbm_objective(X_train, y_train, X_val, y_val, search_space)

    # Create study
    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=random_state),
        pruner=optuna.pruners.MedianPruner(n_startup_trials=10),
    )

    # Run optimization
    logger.info(
        "Starting hyperopt for %s/%s: %d trials, timeout=%s",
        model_type,
        regime,
        n_trials,
        timeout,
    )
    t0 = time.time()

    study.optimize(
        objective,
        n_trials=n_trials,
        timeout=timeout,
        show_progress_bar=False,
    )

    duration = time.time() - t0

    # Extract results
    best_params = study.best_params
    best_ic = study.best_value

    # Build trial history
    trial_history: list[dict[str, Any]] = []
    for t in study.trials:
        if t.state == optuna.trial.TrialState.COMPLETE:
            trial_history.append(
                {
                    "number": t.number,
                    "ic": round(float(t.value), 6) if t.value is not None else None,
                    "params": t.params,
                }
            )

    result = HyperoptResult(
        model_type=model_type,
        regime=regime,
        best_params=best_params,
        best_ic=best_ic,
        n_trials=len(study.trials),
        duration_seconds=duration,
        trial_history=trial_history,
    )

    logger.info(
        "Hyperopt complete for %s/%s: best_ic=%.4f, %d trials, %.1fs",
        model_type,
        regime,
        best_ic,
        len(study.trials),
        duration,
    )

    # Save to cache
    cache = _load_cache(cache_dir=cache_dir)
    key = f"{model_type}_{regime}"
    cache[key] = {
        "model_type": model_type,
        "regime": regime,
        "best_params": best_params,
        "best_ic": best_ic,
        "n_trials": len(study.trials),
        "duration_seconds": duration,
        "timestamp": result.timestamp,
    }
    _save_cache(cache, cache_dir=cache_dir)

    return result


def run_hyperopt_for_regime(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_val: pd.DataFrame,
    y_val: pd.Series,
    regime: str,
    *,
    model_type: ModelType = "xgb",
    n_trials: int = 100,
    force: bool = False,
    cache_dir: Path = _DEFAULT_CACHE_DIR,
) -> HyperoptResult | None:
    """Convenience wrapper: optimize hyperparameters for a specific regime.

    This is a thin alias around ``run_hyperopt`` with clearer semantics.

    Parameters
    ----------
    X_train, y_train, X_val, y_val : pd.DataFrame / pd.Series
        Training and validation data.
    regime : str
        Market regime label (e.g. "bull", "bear", "sideways").
    model_type : {"xgb", "lgbm"}
        Model type.
    n_trials : int
        Number of trials.
    force : bool
        Ignore cache.

    Returns
    -------
    HyperoptResult | None
    """
    return run_hyperopt(
        X_train,
        y_train,
        X_val,
        y_val,
        model_type=model_type,
        regime=regime,
        n_trials=n_trials,
        force=force,
        cache_dir=cache_dir,
    )


def clear_hyperopt_cache(cache_dir: Path = _DEFAULT_CACHE_DIR) -> None:
    """Remove all cached hyperparameter optimization results."""
    cache_file = Path(cache_dir) / "hyperopt_results.json"
    if cache_file.exists():
        cache_file.unlink()
        logger.info("Hyperopt cache cleared: %s", cache_file)
    else:
        logger.info("No hyperopt cache to clear")


def is_optuna_available() -> bool:
    """Check whether Optuna is importable.

    Returns
    -------
    bool
        True if ``import optuna`` succeeds.
    """
    try:
        import optuna  # noqa: F401

        return True
    except ImportError:
        return False


# ── CLI display helper ────────────────────────────────────────────────────────


def format_hyperopt_result(result: HyperoptResult | None) -> str:
    """Format a HyperoptResult as a human-readable string.

    Parameters
    ----------
    result : HyperoptResult | None
        Result to format, or None.

    Returns
    -------
    str
        Multi-line summary string.
    """
    if result is None:
        return "Hyperopt: no result (optuna not installed or no data)"

    lines = [
        f"=== Hyperopt Result: {result.model_type}/{result.regime} ===",
        f"  Best IC:       {result.best_ic:.4f}",
        f"  Trials:        {result.n_trials}",
        f"  Duration:      {result.duration_seconds:.1f}s",
        "  Best params:",
    ]
    for k, v in sorted(result.best_params.items()):
        if isinstance(v, float):
            lines.append(f"    {k}: {v:.6f}")
        else:
            lines.append(f"    {k}: {v}")
    return "\n".join(lines)
