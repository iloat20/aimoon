"""优化后的机器学习训练配置

此配置旨在解决过拟合问题，提供更好的泛化能力。
"""

from __future__ import annotations

from typing import Any

# XGBoost 优化配置 — 强正则化防过拟合
XGB_OPTIMIZED_PARAMS: dict[str, Any] = {
    # 树结构参数 - 严格限制复杂度
    "max_depth": 3,
    "min_child_weight": 50,
    # 学习率和迭代 - 慢学配合多轮
    "learning_rate": 0.01,
    "n_estimators": 2000,
    # 随机性和正则化 - 强防过拟合
    "subsample": 0.5,
    "colsample_bytree": 0.3,
    "reg_lambda": 10.0,
    "reg_alpha": 2.0,
    "gamma": 1.0,
    # 早停配置
    "early_stopping_rounds": 50,
    # 其他
    "objective": "reg:squarederror",
    "eval_metric": "rmse",
    "random_state": 42,
    "verbosity": 0,
}

# LightGBM 优化配置 — 强正则化防过拟合
LGBM_OPTIMIZED_PARAMS: dict[str, Any] = {
    # 树结构参数 - 严格限制复杂度
    "num_leaves": 15,
    "max_depth": 3,
    "min_child_samples": 50,
    # 学习率和迭代 - 慢学配合多轮
    "learning_rate": 0.01,
    "n_estimators": 2000,
    # 随机性和正则化 - 强防过拟合
    "subsample": 0.5,
    "colsample_bytree": 0.3,
    "feature_fraction": 0.3,
    "bagging_fraction": 0.5,
    "reg_lambda": 10.0,
    "reg_alpha": 2.0,
    # 早停配置
    "early_stopping_rounds": 50,
    # 其他
    "objective": "regression",
    "metric": "rmse",
    "random_state": 42,
    "verbose": -1,
    "n_jobs": -1,
}

# 训练配置
TRAINING_CONFIG: dict[str, Any] = {
    # 数据收集 - 最大化数据多样性
    "n_dates": 200,
    "forward_days": 5,
    # 验证配置
    "validation_split": 0.2,
    "cv_folds": 5,
    "purge_gap_multiplier": 2,
    # 特征选择 - 严格筛选
    "feature_selection": {
        "enabled": True,
        "min_ic": 0.01,
        "max_features": 40,
    },
    # 过拟合检测
    "overfit_threshold": 5.0,
}

# 增量学习配置
INCREMENTAL_CONFIG: dict[str, Any] = {
    "warm_start": True,  # 启用增量学习
    "max_incremental_rounds": 100,  # 增量学习最多迭代次数
    "incremental_learning_rate_factor": 0.5,  # 增量学习降低学习率
}

# 特征工程配置
FEATURE_CONFIG: dict[str, Any] = {
    # Alpha360
    "use_alpha360": True,
    "alpha360_window": 60,
    # 技术指标
    "use_technical_features": True,
    "technical_windows": [5, 10, 20],  # 5天、10天、20天窗口
    # 归一化
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


def get_xgb_params(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """获取优化的XGBoost参数，支持覆盖特定参数。"""
    params = XGB_OPTIMIZED_PARAMS.copy()
    if overrides:
        params.update(overrides)
    return params


def get_lgbm_params(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """获取优化的LightGBM参数，支持覆盖特定参数。"""
    params = LGBM_OPTIMIZED_PARAMS.copy()
    if overrides:
        params.update(overrides)
    return params


def get_training_config(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """获取训练配置，支持覆盖特定参数。"""
    config = TRAINING_CONFIG.copy()
    if overrides:
        config.update(overrides)
    return config


# 使用示例
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
