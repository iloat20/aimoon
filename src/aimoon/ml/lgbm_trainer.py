"""LightGBM model trainer — shares feature pipeline with XGBoost."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from aimoon.factors.registry import Registry
from aimoon.ml._training_commons import (
    check_overfit,
    compute_icir,
    compute_shap_top20,
    compute_spearmanr_safe,
    log_training_summary,
    save_model_artifacts,
    should_retrain_on_overfit,
    try_warm_start_lgbm,
)
from aimoon.ml.optimized_config import get_lgbm_params
from aimoon.ml.trainer import TrainingResult, _collect_training_data

logger = logging.getLogger(__name__)

_LGBM_MODEL_FILE = "model.lgbm.txt"


def _default_lgbm_params() -> dict[str, Any]:
    """Return LightGBM hyperparameters from centralized config."""
    return get_lgbm_params()


def _resolve_lgbm_hyperopt_params(
    X: pd.DataFrame,
    y: pd.Series,
    dates_column: pd.Series | None,
    forward_days: int,
    *,
    n_trials: int = 50,
    force: bool = False,
    cache_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Run hyperparameter optimization for LightGBM and return best params.

    Returns None if optuna is not installed or optimization fails.
    """
    try:
        from aimoon.ml.hyperopt import is_optuna_available, run_hyperopt
    except ImportError:
        return None

    if not is_optuna_available():
        return None

    val_size = max(int(len(X) * 0.2), 30)
    X_train = X.iloc[:-val_size]
    y_train = y.iloc[:-val_size]
    X_val = X.iloc[-val_size:]
    y_val = y.iloc[-val_size:]

    result = run_hyperopt(
        X_train,
        y_train,
        X_val,
        y_val,
        model_type="lgbm",
        n_trials=n_trials,
        force=force,
        cache_dir=cache_dir if cache_dir is not None else Path(".aimoon_cache") / "ml",
    )
    if result is None:
        return None
    return result.best_params


def _build_lgbm(params: dict[str, Any], n_estimators: int) -> lgb.LGBMRegressor:
    """Construct LGBMRegressor from params dict."""
    return lgb.LGBMRegressor(
        n_estimators=n_estimators,
        num_leaves=params["num_leaves"],
        learning_rate=params["learning_rate"],
        subsample=params["subsample"],
        colsample_bytree=params["colsample_bytree"],
        reg_lambda=params["reg_lambda"],
        reg_alpha=params["reg_alpha"],
        min_child_samples=params["min_child_samples"],
        random_state=params["random_state"],
        verbose=-1,
        n_jobs=-1,
        max_depth=params.get("max_depth", 4),
    )


def train_lgbm_model(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    registry: Registry | None = None,
    params: dict[str, Any] | None = None,
    n_dates: int = 200,
    forward_days: int = 5,
    save_dir: str | Path | None = None,
    sector_map: dict[str, str] | None = None,
    warm_start: bool = False,
    *,
    use_hyperopt: bool = False,
    hyperopt_trials: int = 50,
    hyperopt_force: bool = False,
    zoo_factor_ids: list[str] | None = None,
) -> TrainingResult:
    """Train LightGBM with purged TimeSeriesSplit cross-validation.

    When ``use_hyperopt`` is True and ``params`` is None, runs Bayesian
    hyperparameter optimization before training.  Requires ``optuna``.
    """
    from aimoon.factors.registry import get_default_registry

    registry = registry or get_default_registry()
    t0 = time.time()

    X, y, _ = _collect_training_data(
        panel,
        klines,
        registry,
        n_dates,
        forward_days,
        sector_map=sector_map,
        zoo_factor_ids=zoo_factor_ids,
    )
    if len(X) < 50 or X.shape[1] < 2:
        raise ValueError(
            f"Insufficient training data: {len(X)} samples, {X.shape[1]} features"
        )

    dates_column = None
    if "_date" in X.columns:
        dates_column = X["_date"].copy()
        X = X.drop(columns=["_date"])

    # Clip labels to reduce outlier impact (与 XGBoost trainer 一致，z-score 单位)
    y = y.clip(-3.0, 3.0)

    # ── Hyperparameter optimization ──────────────────────────────────────
    if params is None and use_hyperopt:
        params = _resolve_lgbm_hyperopt_params(
            X,
            y,
            dates_column,
            forward_days,
            n_trials=hyperopt_trials,
            force=hyperopt_force,
        )
        if params:
            logger.info("Hyperopt: using optimized LightGBM params")

    lgbm_params = {**_default_lgbm_params(), **(params or {})}
    feature_names = X.columns.tolist()

    from aimoon.ml.purged_tscv import PurgedTimeSeriesSplit

    tscv = PurgedTimeSeriesSplit(
        n_splits=8,
        purge_days=forward_days,
        embargo_days=forward_days * 3,
    )
    fold_ics: list[float] = []
    cv_rmse_scores: list[float] = []
    best_cv_score = -999.0
    best_round = 0

    # M2: Use time-series split for final model (consistent with XGBoost)
    if dates_column is not None:
        unique_dates = sorted(dates_column.unique())
        cutoff_idx = max(1, int(len(unique_dates) * 0.8))
        cutoff_idx = min(cutoff_idx, len(unique_dates) - 1)
        cutoff_date = unique_dates[cutoff_idx]
        train_mask = dates_column <= cutoff_date
        val_mask = dates_column > cutoff_date
        X_train_final = X[train_mask]
        X_val_final = X[val_mask]
        y_train_final = y[train_mask]
        y_val_final = y[val_mask]
        logger.info(
            "LightGBM final split: train=%d, val=%d",
            len(X_train_final), len(X_val_final),
        )
    else:
        val_size = int(len(X) * 0.2)
        X_train_final, X_val_final = X.iloc[:-val_size], X.iloc[-val_size:]
        y_train_final, y_val_final = y.iloc[:-val_size], y.iloc[-val_size:]

    X_with_dates = X.copy()
    if dates_column is not None:
        X_with_dates["_date"] = dates_column

    for train_idx, val_idx in tscv.split(X_with_dates, date_column="_date"):
        if len(train_idx) < 10 or len(val_idx) < 5:
            continue

        X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]

        model = _build_lgbm(lgbm_params, lgbm_params["n_estimators"])
        model.fit(
            X_train,
            y_train,
            eval_set=[(X_val, y_val)],
            callbacks=[lgb.early_stopping(20, verbose=False)],
        )
        val_pred = model.predict(X_val)
        fold_ic = compute_spearmanr_safe(val_pred, y_val)
        fold_ics.append(fold_ic)

        # Track actual LightGBM eval metric (RMSE) per fold
        if hasattr(model, "best_score_") and model.best_score_:
            score_val = model.best_score_
            if isinstance(score_val, dict):
                inner = list(score_val.values())[0]
                if isinstance(inner, dict):
                    cv_rmse_scores.append(float(list(inner.values())[0]))
                else:
                    cv_rmse_scores.append(float(inner))
            else:
                cv_rmse_scores.append(float(score_val))

        if fold_ic > best_cv_score:
            best_cv_score = fold_ic
            best_round = model.best_iteration_

    # Warm start
    prev_booster, warm_divisor, feature_names = try_warm_start_lgbm(
        save_dir, feature_names, X, X_train_final, y_train_final, X_val_final, y_val_final,
    )

    num_rounds = (
        min(best_round + 50, lgbm_params["n_estimators"])
        if best_round > 0
        else lgbm_params["n_estimators"]
    )
    if prev_booster is not None:
        num_rounds = max(num_rounds // warm_divisor, 100)

    final_model = _build_lgbm(lgbm_params, num_rounds)
    if prev_booster is not None:
        final_model.fit(
            X_train_final,
            y_train_final,
            eval_set=[(X_val_final, y_val_final)],
            callbacks=[lgb.early_stopping(20, verbose=False)],
            init_model=prev_booster,
        )
    else:
        final_model.fit(
            X_train_final,
            y_train_final,
            eval_set=[(X_val_final, y_val_final)],
            callbacks=[lgb.early_stopping(20, verbose=False)],
        )

    # Overfit detection
    def _lgbm_predict(X_data: pd.DataFrame) -> np.ndarray:
        return final_model.predict(X_data)

    ic, ic_train, overfit_ratio = check_overfit(
        _lgbm_predict,
        X_train_final,
        y_train_final,
        X_val_final,
        y_val_final,
    )

    # Auto-degradation: if warm-start caused severe overfitting, retrain from scratch
    if should_retrain_on_overfit(
        prev_booster is not None, overfit_ratio, model_name="LightGBM"
    ):
        fresh_model = _build_lgbm(lgbm_params, num_rounds)
        fresh_model.fit(
            X_train_final,
            y_train_final,
            eval_set=[(X_val_final, y_val_final)],
            callbacks=[lgb.early_stopping(20, verbose=False)],
        )
        final_model = fresh_model

        ic, ic_train, overfit_ratio = check_overfit(
            lambda X_data: final_model.predict(X_data),
            X_train_final,
            y_train_final,
            X_val_final,
            y_val_final,
        )
        logger.info(
            "LightGBM fresh retrain: val_IC=%.04f, overfit_ratio=%.1f",
            ic,
            overfit_ratio,
        )

    # Feature importance
    importance_arr = final_model.feature_importances_
    total_imp = float(importance_arr.sum())
    importance: dict[str, float] = {}
    if total_imp > 0:
        for name, imp in zip(feature_names, importance_arr):
            importance[name] = float(imp) / total_imp
    importance = dict(sorted(importance.items(), key=lambda x: x[1], reverse=True))

    shap_top20 = compute_shap_top20(final_model, X_val_final, feature_names)
    _, _, fold_icir = compute_icir(fold_ics)

    train_duration = time.time() - t0
    log_training_summary(
        "LightGBM",
        ic,
        ic_train,
        fold_icir,
        X.shape[0],
        X.shape[1],
        train_duration,
    )

    # Save artifacts
    if save_dir is not None:
        save_path = Path(save_dir)

        def _lgbm_save(model: Any, path: str) -> None:
            if hasattr(model, "booster_") and model.booster_ is not None:
                model.booster_.save_model(path)
            else:
                logger.warning(
                    "LGBM booster_ not available, model may not be saved correctly"
                )

        save_model_artifacts(
            save_path,
            final_model,
            feature_names,
            ic=ic,
            ic_train=ic_train,
            fold_ics=fold_ics,
            n_stocks=len(y),
            n_features=X.shape[1],
            n_dates=n_dates,
            forward_days=forward_days,
            train_duration=train_duration,
            cv_scores=cv_rmse_scores if cv_rmse_scores else fold_ics,
            best_cv_score=best_cv_score,
            best_iteration=best_round,
            overfit_ratio=overfit_ratio,
            n_samples_train=len(X_train_final),
            n_samples_val=len(X_val_final),
            shap_top20=shap_top20,
            model_filename=_LGBM_MODEL_FILE,
            feature_filename="lgbm_feature_names.json",
            meta_filename="lgbm_meta.json",
            model_save_fn=_lgbm_save,
        )

    return TrainingResult(
        model=final_model,
        feature_names=tuple(feature_names),
        feature_importance=importance,
        ic=ic,
        n_stocks=len(X),
        n_dates=n_dates,
        train_duration=train_duration,
    )
