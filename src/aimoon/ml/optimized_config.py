"""精简训练配置 — 单 LightGBM, 2 折 Purged TSCV"""

from __future__ import annotations

LGBM_PARAMS = {
    "max_depth": 4,
    "num_leaves": 31,
    "n_estimators": 300,
    "learning_rate": 0.03,
    "min_child_samples": 50,
    "subsample": 0.7,
    "colsample_bytree": 0.6,
    "reg_lambda": 5.0,
    "reg_alpha": 2.0,
    "early_stopping_rounds": 30,
    "objective": "regression",
    "metric": "rmse",
    "random_state": 42,
    "verbose": -1,
    "n_jobs": -1,
}

TRAINING_CONFIG = {
    "n_dates": 120,
    "forward_days": 5,
    "cv_folds": 2,
    "purge_days": 5,
    "embargo_days": 10,
}
