"""Cross-validation training loop for XGBoost models."""

from __future__ import annotations

import logging
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


def run_cv_training(
    X: pd.DataFrame,
    y: pd.Series,
    xgb_params: dict[str, Any],
    feature_names: list[str],
    forward_days: int,
    save_dir: Path | None = None,
) -> tuple[
    xgb.Booster, list[float], list[float], float, int,
    pd.DataFrame, pd.DataFrame, pd.Series, pd.Series,
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
        k: v
        for k, v in xgb_params.items()
        if k not in ("early_stopping_rounds", "n_estimators")
    }

    from aimoon.ml.purged_tscv import PurgedTimeSeriesSplit

    tscv = PurgedTimeSeriesSplit(
        n_splits=8,
        purge_days=forward_days,
        embargo_days=forward_days * 3,
    )
    cv_scores: list[float] = []
    fold_ics: list[float] = []
    best_cv_score = -999.0
    best_round = 0

    # Time-series split for final model (consistent with CV)
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
            "Final model split: train=%d samples (%d dates), val=%d samples (%d dates)",
            len(X_train_final), train_mask.sum(),
            len(X_val_final), val_mask.sum(),
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
        save_dir, feature_names, X, X_train_final, y_train_final, X_val_final, y_val_final,
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
        logger.info(
            "Fresh retrain: val_IC=%.04f, overfit_ratio=%.1f", ic, overfit_ratio
        )

    return (
        final_model, cv_scores, fold_ics, best_cv_score, best_round,
        X_train_final, X_val_final, y_train_final, y_val_final,
    )
