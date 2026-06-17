"""Cross-validation training loop for XGBoost models.

Three training modes:
1. run_cv_training() — standard CV with warm-start (legacy)
2. train_xgboost_with_early_stopping() — CV with consecutive-fold early stopping
   and automatic overfitting detection with complexity reduction
3. run_optuna_search() — Bayesian hyperparameter optimization with 6-fold
   rolling IC mean objective and IC std penalty
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xgboost as xgb

from aimoon.ml._training_commons import (
    check_overfit,
    compute_spearmanr_safe,
    should_retrain_on_overfit,
    try_warm_start_xgb,
)

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_DEFAULT_OVERFIT_THRESHOLD = 1.5
_DEFAULT_EARLY_STOP_PATIENCE = 3
_DEFAULT_MIN_CHILD_WEIGHT_STEP = 10
_DEFAULT_SUBSAMPLE_FLOOR = 0.5
_DEFAULT_MAX_DEPTH_FLOOR = 1
_N_FOLDS_OPTUNA = 5


# ── Standard CV (legacy) ──────────────────────────────────────────────────────


def run_cv_training(
    X: pd.DataFrame,
    y: pd.Series,
    xgb_params: dict[str, Any],
    feature_names: list[str],
    forward_days: int,
    save_dir: Path | None = None,
) -> tuple[
    xgb.Booster,
    list[float],
    list[float],
    float,
    int,
    pd.DataFrame,
    pd.DataFrame,
    pd.Series,
    pd.Series,
]:
    """Run time-series cross-validation and train final XGBoost model.

    Returns (final_model, cv_scores, fold_ics, best_cv_score, best_round,
             X_train_final, X_val_final, y_train_final, y_val_final).
    """
    dates_column = None
    if "_date" in X.columns:
        dates_column = X["_date"].copy()
        X = X.drop(columns=["_date"])

    model_params = {
        k: v for k, v in xgb_params.items() if k not in ("early_stopping_rounds", "n_estimators")
    }

    from aimoon.ml.purged_tscv import PurgedTimeSeriesSplit

    tscv = PurgedTimeSeriesSplit(
        n_splits=6,
        purge_days=forward_days,
        embargo_days=forward_days,
    )
    cv_scores: list[float] = []
    fold_ics: list[float] = []
    best_cv_score = -999.0
    best_round = 0

    # Time-series split for final model (consistent with CV)
    if dates_column is not None:
        unique_dates = sorted(dates_column.unique())
        cutoff_idx = max(1, int(len(unique_dates) * 0.92))
        cutoff_idx = min(cutoff_idx, len(unique_dates) - 1)
        cutoff_date = unique_dates[cutoff_idx]
        train_mask = dates_column <= cutoff_date
        val_mask = dates_column > cutoff_date
        X_train_final = X[train_mask]
        X_val_final = X[val_mask]
        y_train_final = y[train_mask]
        y_val_final = y[val_mask]
        logger.info(
            "Final model split: train=%d samples (%d dates), val=%d samples (%d dates)",
            len(X_train_final),
            train_mask.sum(),
            len(X_val_final),
            val_mask.sum(),
        )
    else:
        val_size = int(len(X) * 0.2)
        X_train_final, X_val_final = X.iloc[:-val_size], X.iloc[-val_size:]
        y_train_final, y_val_final = y.iloc[:-val_size], y.iloc[-val_size:]

    if len(X_train_final) < 10 or len(X_val_final) < 5:
        raise ValueError(
            f"Final split too small: train={len(X_train_final)}, val={len(X_val_final)}"
        )

    X_with_dates = X.copy()
    if dates_column is not None:
        X_with_dates["_date"] = dates_column

    for train_idx, val_idx in tscv.split(X_with_dates, date_column="_date"):
        if len(train_idx) < 10 or len(val_idx) < 5:
            continue

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

        fold_preds = booster.predict(dval)
        fold_ic = compute_spearmanr_safe(fold_preds, y_val)
        fold_ics.append(fold_ic)

        if booster.best_score > best_cv_score:
            best_cv_score = booster.best_score
            best_round = booster.best_iteration

    dtrain_final = xgb.DMatrix(X_train_final, label=y_train_final)

    prev_model, warm_divisor, feature_names = try_warm_start_xgb(
        save_dir,
        feature_names,
        X,
        X_train_final,
        y_train_final,
        X_val_final,
        y_val_final,
    )

    num_rounds = (
        min(best_round + 50, xgb_params["n_estimators"])
        if best_round > 0
        else xgb_params["n_estimators"]
    )
    if prev_model is not None:
        num_rounds = max(num_rounds // warm_divisor, 100)

    final_model = xgb.train(
        model_params,
        dtrain_final,
        num_boost_round=num_rounds,
        xgb_model=prev_model,
        verbose_eval=False,
    )

    def _xgb_predict(X_data: pd.DataFrame) -> np.ndarray:
        return final_model.predict(xgb.DMatrix(X_data))

    ic, ic_train, overfit_ratio = check_overfit(
        _xgb_predict,
        X_train_final,
        y_train_final,
        X_val_final,
        y_val_final,
    )

    if should_retrain_on_overfit(prev_model is not None, overfit_ratio, model_name="XGBoost"):
        final_model = xgb.train(
            model_params,
            dtrain_final,
            num_boost_round=min(best_round + 50, xgb_params["n_estimators"]),
            verbose_eval=False,
        )
        ic, ic_train, overfit_ratio = check_overfit(
            lambda X: final_model.predict(xgb.DMatrix(X)),
            X_train_final,
            y_train_final,
            X_val_final,
            y_val_final,
        )
        logger.info("Fresh retrain: val_IC=%.04f, overfit_ratio=%.1f", ic, overfit_ratio)

    return (
        final_model,
        cv_scores,
        fold_ics,
        best_cv_score,
        best_round,
        X_train_final,
        X_val_final,
        y_train_final,
        y_val_final,
    )


# ── Feature 1: Early stopping on consecutive OOS degradation ─────────────────


def _compute_fold_metrics(
    booster: xgb.Booster,
    dval: xgb.DMatrix,
    y_val: pd.Series,
) -> dict[str, float]:
    """Compute IC, Rank IC, and train/val metrics for a single fold."""
    preds = booster.predict(dval)
    ic = compute_spearmanr_safe(preds, y_val.values)

    # Rank IC: correlation of ranks (more robust to outliers)
    from scipy.stats import rankdata

    pred_ranks = rankdata(preds)
    actual_ranks = rankdata(y_val.values)
    rank_ic = compute_spearmanr_safe(pred_ranks, actual_ranks)

    return {
        "ic": ic,
        "rank_ic": rank_ic,
    }


def _should_early_stop(
    fold_ics: list[float],
    patience: int = _DEFAULT_EARLY_STOP_PATIENCE,
) -> tuple[bool, str]:
    """Check if the last `patience` consecutive folds show OOS degradation.

    Returns (should_stop, reason).
    A fold is considered "degraded" if its IC < previous fold's IC.
    """
    if len(fold_ics) < patience + 1:
        return False, ""

    recent = fold_ics[-(patience + 1) :]
    consecutive_drops = 0
    for i in range(1, len(recent)):
        if recent[i] < recent[i - 1]:
            consecutive_drops += 1
        else:
            consecutive_drops = 0

    if consecutive_drops >= patience:
        trend = " → ".join(f"{ic:.4f}" for ic in recent)
        return True, (
            f"Early stop: {patience} consecutive OOS IC drops. " f"Recent fold ICs: {trend}"
        )
    return False, ""


# ── Feature 2: Overfitting detection with automatic complexity reduction ──────


def _reduce_model_complexity(
    params: dict[str, Any],
    *,
    max_depth_floor: int = _DEFAULT_MAX_DEPTH_FLOOR,
    subsample_floor: float = _DEFAULT_SUBSAMPLE_FLOOR,
    min_child_weight_step: int = _DEFAULT_MIN_CHILD_WEIGHT_STEP,
) -> dict[str, Any]:
    """Return a new params dict with reduced model complexity.

    Reduces max_depth, subsample, and increases min_child_weight.
    """
    new_params = params.copy()

    current_depth = new_params.get("max_depth", 3)
    if current_depth > max_depth_floor:
        new_params["max_depth"] = current_depth - 1
        logger.info("Overfit recovery: max_depth %d → %d", current_depth, new_params["max_depth"])

    current_subsample = new_params.get("subsample", 0.8)
    if current_subsample > subsample_floor:
        new_subsample = max(subsample_floor, current_subsample - 0.1)
        new_params["subsample"] = round(new_subsample, 2)
        logger.info(
            "Overfit recovery: subsample %.2f → %.2f",
            current_subsample,
            new_params["subsample"],
        )

    current_weight = new_params.get("min_child_weight", 10)
    new_params["min_child_weight"] = current_weight + min_child_weight_step
    logger.info(
        "Overfit recovery: min_child_weight %d → %d",
        current_weight,
        new_params["min_child_weight"],
    )

    return new_params


def _detect_and_fix_overfit(
    train_ic: float,
    val_ic: float,
    params: dict[str, Any],
    *,
    threshold: float = _DEFAULT_OVERFIT_THRESHOLD,
) -> tuple[dict[str, Any], bool, float]:
    """Detect overfitting and reduce model complexity if needed.

    Returns (params, was_overfit, overfit_ratio).
    """
    if val_ic <= 1e-10:
        return params, False, 0.0

    ratio = train_ic / val_ic
    is_overfit = ratio > threshold

    if is_overfit:
        logger.warning(
            "Overfitting detected: train_IC=%.4f, val_IC=%.4f, ratio=%.2f > %.1f",
            train_ic,
            val_ic,
            ratio,
            threshold,
        )
        params = _reduce_model_complexity(params)

    return params, is_overfit, ratio


# ── Feature 3: Optuna hyperparameter search ────────────────────────────────────


def _optuna_cv_objective(
    trial: Any,
    X: pd.DataFrame,
    y: pd.Series,
    feature_names: list[str],
    n_folds: int,
    forward_days: int,
    dates_column: pd.Series | None,
) -> float:
    """Optuna objective: 6-fold rolling IC mean with IC std penalty.

    objective = mean(ic_folds) - 0.5 * std(ic_folds)

    The penalty term discourages hyperparameters that produce unstable
    across folds — favoring robust configurations over high-mean/high-variance.
    """
    import xgboost as xgb_local

    # Sample hyperparameters
    params: dict[str, Any] = {
        "max_depth": trial.suggest_int("max_depth", 1, 5),
        "n_estimators": trial.suggest_int("n_estimators", 200, 1200),
        "learning_rate": trial.suggest_float("learning_rate", 0.001, 0.05, log=True),
        "subsample": trial.suggest_float("subsample", 0.5, 1.0),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.3, 1.0),
        "reg_alpha": trial.suggest_float("reg_alpha", 0.0, 10.0),
        "reg_lambda": trial.suggest_float("reg_lambda", 0.0, 10.0),
        "min_child_weight": trial.suggest_int("min_child_weight", 5, 80),
        "gamma": trial.suggest_float("gamma", 0.0, 5.0),
        "objective": "reg:pseudohubererror",
        "eval_metric": "rmse",
        "verbosity": 0,
        "n_jobs": 1,
    }

    model_params = {k: v for k, v in params.items() if k not in ("n_estimators",)}

    # Purged TSCV
    from aimoon.ml.purged_tscv import PurgedTimeSeriesSplit

    tscv = PurgedTimeSeriesSplit(
        n_splits=n_folds,
        purge_days=forward_days,
        embargo_days=forward_days,
    )

    X_with_dates = X.copy()
    if dates_column is not None:
        X_with_dates["_date"] = dates_column

    fold_ics: list[float] = []

    for fold_i, (train_idx, val_idx) in enumerate(tscv.split(X_with_dates, date_column="_date")):
        if len(train_idx) < 10 or len(val_idx) < 5:
            continue

        X_train = X.iloc[train_idx]
        X_val = X.iloc[val_idx]
        y_train = y.iloc[train_idx]
        y_val = y.iloc[val_idx]

        dtrain = xgb_local.DMatrix(X_train, label=y_train)
        dval = xgb_local.DMatrix(X_val, label=y_val)

        try:
            booster = xgb_local.train(
                model_params,
                dtrain,
                num_boost_round=params["n_estimators"],
                evals=[(dval, "val")],
                early_stopping_rounds=30,
                verbose_eval=False,
            )
            preds = booster.predict(dval)
            ic = compute_spearmanr_safe(preds, y_val.values)
            fold_ics.append(ic)
        except Exception:
            fold_ics.append(0.0)

        # Optuna pruning: report intermediate value
        if len(fold_ics) >= 2:
            intermediate = float(np.mean(fold_ics))
            trial.report(intermediate, fold_i)
            if trial.should_prune():
                import optuna

                raise optuna.TrialPruned()

    if not fold_ics:
        return 0.0

    mean_ic = float(np.mean(fold_ics))
    std_ic = float(np.std(fold_ics)) if len(fold_ics) > 1 else 0.0

    # Objective: maximize mean IC, penalize high variance
    objective_value = mean_ic - 0.5 * std_ic

    # Store extra info in trial
    trial.set_user_attr("mean_ic", round(mean_ic, 6))
    trial.set_user_attr("std_ic", round(std_ic, 6))
    trial.set_user_attr("fold_ics", [round(ic, 4) for ic in fold_ics])

    return objective_value


def run_optuna_search(
    X: pd.DataFrame,
    y: pd.Series,
    feature_names: list[str],
    forward_days: int = 22,
    n_folds: int = _N_FOLDS_OPTUNA,
    n_trials: int = 80,
    timeout: float | None = None,
    random_state: int = 42,
    *,
    regime: str = "all",
    cache_dir: str | Path = ".aimoon_cache",
    force: bool = False,
) -> dict[str, Any] | None:
    """Run Optuna hyperparameter search with 6-fold rolling IC objective.

    Objective = mean(IC) - 0.5 * std(IC) across Purged TSCV folds.

    Parameters
    ----------
    X, y : Training data.
    feature_names : Feature column names.
    forward_days : Forward days for purge/embargo.
    n_folds : Number of Purged TSCV folds (default 6).
    n_trials : Number of Optuna trials.
    timeout : Max seconds (None = no limit).
    random_state : Seed.
    regime : Market regime label for caching.
    cache_dir : Cache directory.
    force : Ignore cache and re-run.

    Returns
    -------
    dict | None with keys: best_params, best_objective, mean_ic, std_ic,
    fold_ics, n_trials, regime. None if Optuna not installed.
    """
    try:
        import optuna
    except ImportError:
        logger.warning(
            "optuna not installed — skipping hyperparameter search. "
            "Install with: pip install optuna"
        )
        return None

    cache_path = Path(cache_dir) / "ml" / "hyperopt_results.json"
    if not force and cache_path.exists():
        try:
            import json

            with open(cache_path, encoding="utf-8") as f:
                cache = json.load(f)
            key = f"xgb_{regime}"
            if key in cache:
                cached = cache[key]
                age_days = (time.time() - cached.get("timestamp", 0)) / 86400
                if age_days < 7:
                    logger.info("Using cached Optuna params for xgb/%s", regime)
                    return cached
        except Exception:
            pass

    dates_column = None
    if "_date" in X.columns:
        dates_column = X["_date"].copy()
        X = X.drop(columns=["_date"])

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=random_state),
        pruner=optuna.pruners.MedianPruner(
            n_startup_trials=10,
            n_warmup_steps=2,
        ),
    )

    logger.info(
        "Starting Optuna search: %d trials, %d folds, regime=%s",
        n_trials,
        n_folds,
        regime,
    )

    import time as _time

    t0 = _time.time()

    study.optimize(
        lambda trial: _optuna_cv_objective(
            trial,
            X,
            y,
            feature_names,
            n_folds,
            forward_days,
            dates_column,
        ),
        n_trials=n_trials,
        timeout=timeout,
        show_progress_bar=False,
    )

    duration = _time.time() - t0

    # Extract best result
    best_trial = study.best_trial
    best_params = best_trial.params
    best_objective = best_trial.value
    mean_ic = best_trial.user_attrs.get("mean_ic", 0.0)
    std_ic = best_trial.user_attrs.get("std_ic", 0.0)
    fold_ics = best_trial.user_attrs.get("fold_ics", [])

    result = {
        "best_params": best_params,
        "best_objective": round(best_objective, 6),
        "mean_ic": round(mean_ic, 6),
        "std_ic": round(std_ic, 6),
        "fold_ics": fold_ics,
        "n_trials": len(study.trials),
        "n_folds": n_folds,
        "regime": regime,
        "duration_seconds": round(duration, 1),
        "all_trial_objectives": [
            round(t.value, 4)
            for t in study.trials
            if t.state == optuna.trial.TrialState.COMPLETE and t.value is not None
        ],
    }

    logger.info(
        "Optuna search complete: best_objective=%.4f, mean_ic=%.4f, std_ic=%.4f, "
        "%d trials, %.1fs",
        best_objective,
        mean_ic,
        std_ic,
        len(study.trials),
        duration,
    )

    # Cache result
    try:
        import json

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache: dict[str, Any] = {}
        if cache_path.exists():
            with open(cache_path, encoding="utf-8") as f:
                cache = json.load(f)
        cache[f"xgb_{regime}"] = result
        tmp = cache_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False, default=str)
        tmp.replace(cache_path)
    except Exception as e:
        logger.debug("Failed to cache Optuna result: %s", e)

    return result


# ── Feature 1+2 combined: train_xgboost_with_early_stopping ───────────────────


def train_xgboost_with_early_stopping(
    X: pd.DataFrame,
    y: pd.Series,
    xgb_params: dict[str, Any],
    feature_names: list[str],
    forward_days: int,
    *,
    early_stop_patience: int = _DEFAULT_EARLY_STOP_PATIENCE,
    overfit_threshold: float = _DEFAULT_OVERFIT_THRESHOLD,
    save_dir: Path | None = None,
) -> tuple[
    xgb.Booster,
    list[float],
    list[float],
    list[float],
    float,
    int,
    pd.DataFrame,
    pd.DataFrame,
    pd.Series,
    pd.Series,
    list[dict[str, Any]],
]:
    """Train XGBoost with consecutive-fold early stopping + overfitting detection.

    Enhancements over ``run_cv_training``:
    1. **Early stopping across folds**: If OOS IC drops for
       ``early_stop_patience`` consecutive folds, stop CV and skip remaining folds.
    2. **Per-fold IC + Rank IC**: Each fold records both Spearman IC and Rank IC.
    3. **Overfitting auto-recovery**: If train/val IC ratio > ``overfit_threshold``,
       automatically reduce model complexity (max_depth--, subsample--, min_child_weight++)
       and retrain the final model.

    Parameters
    ----------
    X, y : Training data (including ``_date`` column if date-based splits desired).
    xgb_params : XGBoost hyperparameters.
    feature_names : Feature column names.
    forward_days : Purge/embargo window in trading days.
    early_stop_patience : Consecutive OOS IC drops before stopping.
    overfit_threshold : Train/val IC ratio threshold for complexity reduction.
    save_dir : Directory for warm-start model caching.

    Returns
    -------
    tuple of:
        final_model : xgb.Booster
        val_ics : list[float]  — per-fold validation ICs
        train_ics : list[float]  — per-fold train ICs
        rank_ics : list[float]  — per-fold Rank ICs
        best_cv_score : float
        best_round : int
        X_train_final, X_val_final : pd.DataFrame
        y_train_final, y_val_final : pd.Series
        fold_details : list[dict]  — per-fold metadata (ic, rank_ic, train_ic, overfit)
    """
    dates_column = None
    if "_date" in X.columns:
        dates_column = X["_date"].copy()
        X = X.drop(columns=["_date"])

    model_params = {
        k: v for k, v in xgb_params.items() if k not in ("early_stopping_rounds", "n_estimators")
    }

    from aimoon.ml.purged_tscv import PurgedTimeSeriesSplit

    tscv = PurgedTimeSeriesSplit(
        n_splits=6,
        purge_days=forward_days,
        embargo_days=forward_days,
    )

    val_ics: list[float] = []
    train_ics: list[float] = []
    rank_ics: list[float] = []
    fold_details: list[dict[str, Any]] = []
    best_cv_score = -999.0
    best_round = 0
    stopped_early = False
    stop_reason = ""

    # ── Final model split (80/20 time-series) ──
    if dates_column is not None:
        unique_dates = sorted(dates_column.unique())
        cutoff_idx = max(1, int(len(unique_dates) * 0.92))
        cutoff_idx = min(cutoff_idx, len(unique_dates) - 1)
        cutoff_date = unique_dates[cutoff_idx]
        train_mask = dates_column <= cutoff_date
        val_mask = dates_column > cutoff_date
        X_train_final = X[train_mask]
        X_val_final = X[val_mask]
        y_train_final = y[train_mask]
        y_val_final = y[val_mask]
    else:
        val_size = int(len(X) * 0.2)
        X_train_final, X_val_final = X.iloc[:-val_size], X.iloc[-val_size:]
        y_train_final, y_val_final = y.iloc[:-val_size], y.iloc[-val_size:]

    if len(X_train_final) < 10 or len(X_val_final) < 5:
        raise ValueError(
            f"Final split too small: train={len(X_train_final)}, val={len(X_val_final)}"
        )

    X_with_dates = X.copy()
    if dates_column is not None:
        X_with_dates["_date"] = dates_column

    # ── CV loop with early stopping ──
    for train_idx, val_idx in tscv.split(X_with_dates, date_column="_date"):
        if len(train_idx) < 10 or len(val_idx) < 5:
            continue

        X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]

        dtrain = xgb.DMatrix(X_train, label=y_train)
        dval = xgb.DMatrix(X_val, label=y_val)

        booster = xgb.train(
            model_params,
            dtrain,
            num_boost_round=xgb_params["n_estimators"],
            evals=[(dval, "val")],
            early_stopping_rounds=xgb_params.get("early_stopping_rounds", 30),
            verbose_eval=False,
        )

        # Compute per-fold metrics
        val_preds = booster.predict(dval)
        val_ic = compute_spearmanr_safe(val_preds, y_val.values)

        train_preds = booster.predict(dtrain)
        train_ic = compute_spearmanr_safe(train_preds, y_train.values)

        from scipy.stats import rankdata

        rank_ic = compute_spearmanr_safe(rankdata(val_preds), rankdata(y_val.values))

        val_ics.append(val_ic)
        train_ics.append(train_ic)
        rank_ics.append(rank_ic)

        fold_info: dict[str, Any] = {
            "val_ic": round(val_ic, 4),
            "train_ic": round(train_ic, 4),
            "rank_ic": round(rank_ic, 4),
            "best_score": float(booster.best_score),
            "best_iteration": int(booster.best_iteration),
            "n_train": len(train_idx),
            "n_val": len(val_idx),
            "overfit_ratio": round(train_ic / (val_ic + 1e-10), 2),
        }
        fold_details.append(fold_info)

        if booster.best_score > best_cv_score:
            best_cv_score = float(booster.best_score)
            best_round = int(booster.best_iteration)

        # ── Early stopping check ──
        should_stop, reason = _should_early_stop(val_ics, patience=early_stop_patience)
        if should_stop:
            stopped_early = True
            stop_reason = reason
            logger.warning(reason)
            break

    n_completed_folds = len(val_ics)
    logger.info(
        "CV complete: %d/%d folds%s, best_cv_score=%.1f, best_round=%d",
        n_completed_folds,
        tscv.get_n_splits(),
        (f" (stopped early: {stop_reason})" if stopped_early else ""),
        best_cv_score,
        best_round,
    )

    # ── Final model training ──
    dtrain_final = xgb.DMatrix(X_train_final, label=y_train_final)

    prev_model, warm_divisor, feature_names = try_warm_start_xgb(
        save_dir,
        feature_names,
        X,
        X_train_final,
        y_train_final,
        X_val_final,
        y_val_final,
    )

    num_rounds = (
        min(best_round + 50, xgb_params["n_estimators"])
        if best_round > 0
        else xgb_params["n_estimators"]
    )
    if prev_model is not None:
        num_rounds = max(num_rounds // warm_divisor, 100)

    final_model = xgb.train(
        model_params,
        dtrain_final,
        num_boost_round=num_rounds,
        xgb_model=prev_model,
        verbose_eval=False,
    )

    # ── Overfitting detection with auto-recovery ──
    def _predict_fn(X_data: pd.DataFrame) -> np.ndarray:
        return final_model.predict(xgb.DMatrix(X_data))

    ic_val_final, ic_train_final, overfit_ratio = check_overfit(
        _predict_fn,
        X_train_final,
        y_train_final,
        X_val_final,
        y_val_final,
    )

    if overfit_ratio > overfit_threshold:
        # Reduce complexity and retrain
        reduced_params = _reduce_model_complexity(
            model_params,
            max_depth_floor=_DEFAULT_MAX_DEPTH_FLOOR,
            subsample_floor=_DEFAULT_SUBSAMPLE_FLOOR,
        )
        # Also reduce num_rounds to match simpler model
        simpler_rounds = max(num_rounds // 2, 100)

        logger.info(
            "Overfit recovery: retraining with reduced complexity, " "rounds %d → %d",
            num_rounds,
            simpler_rounds,
        )

        final_model = xgb.train(
            reduced_params,
            dtrain_final,
            num_boost_round=simpler_rounds,
            verbose_eval=False,
        )

        ic_val_final, ic_train_final, overfit_ratio = check_overfit(
            _predict_fn,
            X_train_final,
            y_train_final,
            X_val_final,
            y_val_final,
        )
        logger.info(
            "Post-recovery: val_IC=%.4f, train_IC=%.4f, ratio=%.2f",
            ic_val_final,
            ic_train_final,
            overfit_ratio,
        )

    return (
        final_model,
        val_ics,
        train_ics,
        rank_ics,
        best_cv_score,
        best_round,
        X_train_final,
        X_val_final,
        y_train_final,
        y_val_final,
        fold_details,
    )
