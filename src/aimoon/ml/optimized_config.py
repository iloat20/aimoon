"""优化后的机器学习训练配置

此配置旨在解决过拟合问题，提供更好的泛化能力。
支持通过 hyperopt.py 进行贝叶斯超参数优化，自动覆盖默认参数。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── 默认超参数（可通过 hyperopt 覆盖）──────────────────────────────────────────

# XGBoost 优化配置 — 平衡正则化与表达能力
XGB_OPTIMIZED_PARAMS: dict[str, Any] = {
    # 树结构参数
    "max_depth": 3,
    "min_child_weight": 10,
    # 学习率和迭代
    "learning_rate": 0.01,
    "n_estimators": 500,
    # 随机性和正则化
    "subsample": 0.8,
    "colsample_bytree": 0.5,
    "reg_lambda": 2.0,
    "reg_alpha": 0.5,
    "gamma": 0.1,
    # 早停
    "early_stopping_rounds": 30,
    # 目标函数 — Huber 对极端值鲁棒
    "objective": "reg:pseudohubererror",
    "huber_slope": 1.0,
    "eval_metric": "rmse",
}

# LightGBM 优化配置 — 适度正则化（诊断修复：原配置严重欠拟合）
# 原 num_leaves=7, subsample=0.3, colsample_bytree=0.15, reg_lambda=30 导致模型无法学习
LGBM_OPTIMIZED_PARAMS: dict[str, Any] = {
    # 树结构参数 — num_leaves 控制复杂度
    "num_leaves": 21,
    "min_child_samples": 50,
    # 学习率和迭代
    "learning_rate": 0.02,
    "n_estimators": 500,
    # 随机性和正则化 — 适度采样，不过度限制
    "subsample": 0.7,
    "colsample_bytree": 0.6,
    "feature_fraction": 0.6,
    "bagging_fraction": 0.7,
    "reg_lambda": 5.0,
    "reg_alpha": 2.0,
    # 早停
    "early_stopping_rounds": 30,
    # 其他
    "objective": "regression",
    "metric": "rmse",
    "random_state": 42,
    "verbose": -1,
    "n_jobs": -1,
}

# 训练配置 — L5: 统一 n_dates=200（与 trainer.py 默认值一致）
TRAINING_CONFIG: dict[str, Any] = {
    # 数据收集
    "n_dates": 300,
    "forward_days": 5,
    # 验证配置
    "validation_split": 0.25,
    "cv_folds": 3,
    "purge_gap_multiplier": 3,
    # 特征选择 — H3: min_ic 从 0.01 提高到 0.025
    "feature_selection": {
        "enabled": True,
        "min_ic": 0.03,
        "max_features": 80,
        "variance_threshold": 0.01,
        "correlation_threshold": 0.85,
        "use_l1": False,
    },
    # 超参数优化
    "hyperopt": {
        "enabled": False,  # 默认关闭，CLI --hyperopt 开启
        "n_trials": 50,
        "timeout": None,  # 秒，None = 无限制
        "regime": "all",
    },
    # 过拟合检测
    "overfit_threshold": 5.0,
}

# 增量学习配置
INCREMENTAL_CONFIG: dict[str, Any] = {
    "warm_start": True,
    "max_incremental_rounds": 100,
    "incremental_learning_rate_factor": 0.5,
}

# 智能增量学习配置 — A/B 双模型 + EWC 正则 + 自适应权重
SMART_INCREMENTAL_CONFIG: dict[str, Any] = {
    # EWC 正则
    "ewc_lambda": 50.0,
    "fisher_samples": 200,
    # IC 衰减检测
    "ic_decay_threshold": 0.02,
    "ic_decay_window": 20,
    # 性能滑坡检测
    "slide_threshold": 0.3,
    "slide_n_splits": 5,
    "slide_purge_days": 5,
    "slide_embargo_days": 15,
    # 训练控制
    "incremental_rounds": 100,
    "a_retrain_days": 7,
    "b_retrain_on_slide": True,
    # 权重分配
    "weight_boost_on_decay": 0.7,
    "weight_normal": 0.7,
    "min_weight_b": 0.1,
}

# 特征工程配置
FEATURE_CONFIG: dict[str, Any] = {
    "use_alpha360": True,
    "alpha360_window": 60,
    "use_technical_features": True,
    "technical_windows": [5, 10, 20],
    "normalize_features": True,
    "use_robust_zscore": True,
    "zscore_clip": 3.0,
}

# 输出配置
OUTPUT_CONFIG: dict[str, Any] = {
    "save_dir": ".aimoon_cache/ml",
    "model_ttl_days": 7,
    "save_meta": True,
    "save_feature_importance": True,
}

# Stacking Ensemble 配置
STACKING_CONFIG: dict[str, Any] = {
    "n_splits": 5,
    "purge_days": 5,
    "embargo_days": 10,
    "xgb_params": {
        "objective": "binary:logistic",
        "eval_metric": "auc",
        "max_depth": 5,
        "learning_rate": 0.05,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "n_estimators": 200,
        "verbosity": 0,
    },
    "lgbm_params": {
        "objective": "binary",
        "metric": "auc",
        "max_depth": 5,
        "learning_rate": 0.05,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "n_estimators": 200,
        "verbose": -1,
    },
    "xgb_regression_params": {
        "objective": "reg:squarederror",
        "eval_metric": "rmse",
        "max_depth": 5,
        "learning_rate": 0.05,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "n_estimators": 200,
        "verbosity": 0,
    },
    "lgbm_regression_params": {
        "objective": "regression",
        "metric": "rmse",
        "max_depth": 5,
        "learning_rate": 0.05,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "n_estimators": 200,
        "verbose": -1,
    },
    "meta_n_estimators": 50,
}


def get_stacking_params() -> dict[str, Any]:
    """获取 Stacking Ensemble 配置。"""
    return STACKING_CONFIG.copy()


# ── 参数获取函数 ──────────────────────────────────────────────────────────────


def get_xgb_params(
    overrides: dict[str, Any] | None = None,
    use_hyperopt: bool = False,
    model_type: str = "xgb",
    regime: str = "all",
    n_trials: int = 50,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    """获取优化的XGBoost参数，支持 hyperopt 覆盖。

    Parameters
    ----------
    overrides : dict | None
        手动覆盖参数。
    use_hyperopt : bool
        是否尝试使用 hyperopt 优化参数。
    model_type : str
        用于 hyperopt 缓存键。
    regime : str
        市场状态标签。
    n_trials : int
        hyperopt 搜索次数。

    Returns
    -------
    dict[str, Any]
        最终参数。
    """
    params = XGB_OPTIMIZED_PARAMS.copy()

    # 尝试 hyperopt 优化
    if use_hyperopt:
        try:
            from aimoon.ml.hyperopt import get_best_params, is_optuna_available

            if is_optuna_available():
                kwargs: dict[str, Any] = {"model_type": model_type, "regime": regime}
                if cache_dir is not None:
                    kwargs["cache_dir"] = cache_dir
                best = get_best_params(**kwargs)
                if best:
                    params.update(best)
                    logger.info("Using hyperopt params for %s/%s", model_type, regime)
        except Exception as e:
            logger.warning("Hyperopt lookup failed: %s", e)

    if overrides:
        params.update(overrides)
    return params


def get_lgbm_params(
    overrides: dict[str, Any] | None = None,
    use_hyperopt: bool = False,
    model_type: str = "lgbm",
    regime: str = "all",
    n_trials: int = 50,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    """获取优化的LightGBM参数，支持 hyperopt 覆盖。

    Parameters
    ----------
    overrides : dict | None
        手动覆盖参数。
    use_hyperopt : bool
        是否尝试使用 hyperopt 优化参数。
    model_type : str
        用于 hyperopt 缓存键。
    regime : str
        市场状态标签。
    n_trials : int
        hyperopt 搜索次数。

    Returns
    -------
    dict[str, Any]
        最终参数。
    """
    params = LGBM_OPTIMIZED_PARAMS.copy()

    if use_hyperopt:
        try:
            from aimoon.ml.hyperopt import get_best_params, is_optuna_available

            if is_optuna_available():
                kwargs_lgbm: dict[str, Any] = {"model_type": model_type, "regime": regime}
                if cache_dir is not None:
                    kwargs_lgbm["cache_dir"] = cache_dir
                best = get_best_params(**kwargs_lgbm)
                if best:
                    params.update(best)
                    logger.info("Using hyperopt params for %s/%s", model_type, regime)
        except Exception as e:
            logger.warning("Hyperopt lookup failed: %s", e)

    if overrides:
        params.update(overrides)
    return params


def get_training_config(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """获取训练配置，支持覆盖特定参数。"""
    config = TRAINING_CONFIG.copy()
    if overrides:
        config.update(overrides)
    return config


# ── 使用示例 ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    print("=== XGBoost 优化配置 ===")
    print(json.dumps(XGB_OPTIMIZED_PARAMS, indent=2, ensure_ascii=False))

    print("\n=== LightGBM 优化配置 ===")
    print(json.dumps(LGBM_OPTIMIZED_PARAMS, indent=2, ensure_ascii=False))

    print("\n=== 训练配置 ===")
    print(json.dumps(TRAINING_CONFIG, indent=2, ensure_ascii=False))

    print("\n=== 使用示例 ===")
    xgb_params = get_xgb_params({"learning_rate": 0.05})
    print(f"XGBoost with custom learning_rate: {xgb_params['learning_rate']}")

    lgbm_params = get_lgbm_params({"n_estimators": 1000})
    print(f"LightGBM with custom n_estimators: {lgbm_params['n_estimators']}")

    # 使用 hyperopt
    xgb_hyperopt = get_xgb_params(use_hyperopt=True)
    print(f"XGBoost with hyperopt: {xgb_hyperopt}")
