"""LightGBM model trainer — shares feature pipeline with XGBoost."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from aimoon.ml._training_commons import (
    check_overfit,
    compute_icir,
    compute_shap_top20,
    features_compatible,
    save_training_meta,
)
from aimoon.factors.registry import Registry
from aimoon.ml.optimized_config import get_lgbm_params
from aimoon.ml.trainer import TrainingResult, _collect_training_data

logger = logging.getLogger(__name__)

_LGBM_MODEL_FILE = "model.lgbm.txt"


def _default_lgbm_params() -> dict[str, Any]:
    """Return LightGBM hyperparameters from centralized config."""
    return get_lgbm_params()


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
) -> TrainingResult:
    """Train LightGBM with purged TimeSeriesSplit cross-validation."""
    from aimoon.factors.registry import get_default_registry

    registry = registry or get_default_registry()
    t0 = time.time()

    X, y = _collect_training_data(
        panel,
        klines,
        registry,
        n_dates,
        forward_days,
        sector_map=sector_map,
    )
    if len(X) < 50 or X.shape[1] < 2:
        raise ValueError(f"Insufficient training data: {len(X)} samples, {X.shape[1]} features")

    dates_column = None
    if "_date" in X.columns:
        dates_column = X["_date"].copy()
        X = X.drop(columns=["_date"])

    lgbm_params = {**_default_lgbm_params(), **(params or {})}
    feature_names = X.columns.tolist()

    from aimoon.ml.purged_tscv import PurgedTimeSeriesSplit

    tscv = PurgedTimeSeriesSplit(
        n_splits=5,
        purge_days=forward_days,
        embargo_days=forward_days,
    )
    fold_ics: list[float] = []
    cv_rmse_scores: list[float] = []
    best_cv_score = -999.0
    best_round = 0

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
        ic, _ = spearmanr(val_pred, y_val)
        fold_ic = float(ic) if not np.isnan(ic) else 0.0
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
    prev_booster = None
    if warm_start and save_dir is not None:
        prev_path = Path(save_dir) / _LGBM_MODEL_FILE
        if prev_path.exists():
            try:
                prev_booster = lgb.Booster(model_file=str(prev_path))
                prev_features = prev_booster.feature_name()
                if not features_compatible(prev_features, feature_names):
                    logger.info(
                        "LightGBM warm start discarded: feature mismatch (old=%d, new=%d)",
                        len(prev_features or []),
                        len(feature_names),
                    )
                    prev_booster = None
                else:
                    # Reorder features to match previous model's column order
                    if prev_features and list(prev_features) != feature_names:
                        X = X[list(prev_features)]
                        X_train_final = X.iloc[:-val_size]
                        X_val_final = X.iloc[-val_size:]
                        feature_names = list(prev_features)
                        logger.info("LightGBM warm start: reordered features")
                    logger.info("LightGBM warm start: continuing from previous model")
            except Exception as e:
                logger.warning("LightGBM warm start failed, training from scratch: %s", e)
                prev_booster = None

    num_rounds = (
        min(best_round + 50, lgbm_params["n_estimators"])
        if best_round > 0
        else lgbm_params["n_estimators"]
    )
    if prev_booster:
        num_rounds = max(num_rounds // 3, 100)

    final_model = _build_lgbm(lgbm_params, num_rounds)
    if prev_booster is not None:
        final_model.fit(X_train_final, y_train_final, init_model=prev_booster)
    else:
        final_model.fit(X_train_final, y_train_final)

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
    if prev_booster is not None and overfit_ratio > 5.0:
        logger.warning(
            "LightGBM overfit ratio %.1f > 5.0 with warm-start, retraining from scratch",
            overfit_ratio,
        )
        fresh_model = _build_lgbm(lgbm_params, num_rounds)
        fresh_model.fit(X_train_final, y_train_final)
        final_model = fresh_model

        def _fresh_predict(X_data: pd.DataFrame) -> np.ndarray:
            return final_model.predict(X_data)

        ic, ic_train, overfit_ratio = check_overfit(
            _fresh_predict,
            X_train_final,
            y_train_final,
            X_val_final,
            y_val_final,
        )
        logger.info("LightGBM fresh retrain: val_IC=%.04f, overfit_ratio=%.1f", ic, overfit_ratio)

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
    logger.info(
        "LightGBM trained: val_IC=%.04f, train_IC=%.04f, ICIR=%.04f, "
        "%d samples x %d features, %.1fs",
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
        save_path.mkdir(parents=True, exist_ok=True)
        final_model.booster_.save_model(str(save_path / _LGBM_MODEL_FILE))
        fn_path = save_path / "feature_names.json"
        if not fn_path.exists():
            with open(fn_path, "w", encoding="utf-8") as f:
                json.dump(feature_names, f)
        save_training_meta(
            save_path,
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
            filename="lgbm_meta.json",
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
