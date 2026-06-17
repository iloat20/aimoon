"""精简训练循环 — 2 折 Purged TSCV LightGBM"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from aimoon.ml._training_commons import compute_spearmanr_safe
from aimoon.ml.purged_tscv import PurgedTimeSeriesSplit

logger = logging.getLogger(__name__)


def run_cv_training_lgbm(
    X: pd.DataFrame,
    y: pd.Series,
    lgbm_params: dict[str, Any],
    n_dates: int,
    save_dir: Path | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Run 2-fold Purged TSCV and return (final_model, cv_meta).

    cv_meta contains: fold_ics, mean_ic, n_stocks, n_dates.
    """
    dates_column = None
    if "_date" in X.columns:
        dates_column = X["_date"].copy()
        X = X.drop(columns=["_date"])

    tscv = PurgedTimeSeriesSplit(
        n_splits=2,
        purge_days=5,
        embargo_days=10,
    )

    fold_ics: list[float] = []

    X_with_dates = X.copy()
    if dates_column is not None:
        X_with_dates["_date"] = dates_column

    for train_idx, val_idx in tscv.split(X_with_dates, date_column="_date"):
        if len(train_idx) < 10 or len(val_idx) < 5:
            continue

        X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]

        train_data = lgb.Dataset(X_train, label=y_train)
        val_data = lgb.Dataset(X_val, label=y_val, reference=train_data)

        model = lgb.train(
            lgbm_params,
            train_data,
            valid_sets=[val_data],
            num_boost_round=lgbm_params.get("n_estimators", 300),
            callbacks=[
                lgb.early_stopping(stopping_rounds=lgbm_params.get("early_stopping_rounds", 30)),
                lgb.log_evaluation(0),
            ],
        )

        preds = model.predict(X_val, num_iteration=model.best_iteration)
        ic = compute_spearmanr_safe(preds, y_val.values)
        fold_ics.append(float(ic))

    logger.info(
        "CV complete: %d folds, mean_IC=%.4f",
        len(fold_ics),
        np.mean(fold_ics) if fold_ics else 0,
    )

    # Train final model on all data
    train_data_full = lgb.Dataset(X, label=y)
    final_model = lgb.train(
        lgbm_params,
        train_data_full,
        num_boost_round=lgbm_params.get("n_estimators", 300),
    )

    cv_meta = {
        "fold_ics": fold_ics,
        "mean_ic": float(np.mean(fold_ics)) if fold_ics else 0.0,
        "n_stocks": len(y),
        "n_dates": n_dates,
    }

    return final_model, cv_meta
