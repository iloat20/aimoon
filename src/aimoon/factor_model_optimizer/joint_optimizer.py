"""联合优化框架 — Optuna 同时优化因子参数 + 模型超参数 + 预测周期。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

logger = logging.getLogger(__name__)


def _spearmanr_safe(pred: np.ndarray, actual: np.ndarray) -> float:
    """安全的 Spearman 相关系数。"""
    if len(pred) < 5:
        return 0.0
    mask = ~(np.isnan(pred) | np.isnan(actual))
    if mask.sum() < 5:
        return 0.0
    ic, _ = spearmanr(pred[mask], actual[mask])
    return float(ic) if not np.isnan(ic) else 0.0


def _compute_forward_returns(close: pd.DataFrame, forward_days: int) -> pd.DataFrame:
    """计算前向收益率: (close[t+N] / close[t]) - 1。"""
    return close.shift(-forward_days) / close - 1.0


def _build_lgbm(params: dict[str, Any]) -> lgb.LGBMRegressor:
    """从参数字典构建 LGBMRegressor。"""
    return lgb.LGBMRegressor(
        n_estimators=params.get("n_estimators", 500),
        num_leaves=params.get("num_leaves", 31),
        max_depth=params.get("max_depth", 4),
        learning_rate=params.get("learning_rate", 0.01),
        subsample=params.get("subsample", 0.8),
        colsample_bytree=params.get("colsample_bytree", 0.8),
        reg_alpha=params.get("reg_alpha", 0.0),
        reg_lambda=params.get("reg_lambda", 0.0),
        min_child_samples=params.get("min_child_samples", 20),
        random_state=params.get("random_state", 42),
        verbose=-1,
        n_jobs=-1,
    )


@dataclass
class OptimizationResult:
    """优化结果。"""

    best_params: dict[str, Any]
    best_val_sharpe: float
    train_sharpe: float
    overfit_ratio: float
    selected_factors: list[str]
    forward_days: int
    n_trials: int
    duration_seconds: float
    trial_history: list[dict[str, Any]] = field(default_factory=list)


class JointOptimizer:
    """联合优化器: 同时优化因子参数、模型超参数、预测周期。"""

    def __init__(self, config: Any = None) -> None:
        from aimoon.factor_model_optimizer.config import OptimizerConfig

        self.config = config or OptimizerConfig()

    def optimize(
        self,
        panel: dict[str, pd.DataFrame],
        factor_df: pd.DataFrame | None = None,
        factor_defs: list[Any] | None = None,
    ) -> OptimizationResult:
        """运行联合优化。"""
        try:
            import optuna
        except ImportError:
            raise ImportError(
                "optuna is required for joint optimization. " "Install with: pip install optuna"
            )

        from aimoon.factor_model_optimizer.factor_engine import (
            compute_all_factors,
        )

        t0 = time.time()

        if factor_df is None or factor_defs is None:
            factor_df, factor_defs = compute_all_factors(panel, self.config)

        close = panel["close"]

        all_dates = sorted(factor_df["date"].unique())
        n_dates = len(all_dates)
        train_end = int(n_dates * self.config.train_ratio)
        val_end = int(n_dates * (self.config.train_ratio + self.config.val_ratio))

        train_dates = all_dates[:train_end]
        val_dates = all_dates[train_end:val_end]

        train_df = factor_df[factor_df["date"].isin(train_dates)]
        val_df = factor_df[factor_df["date"].isin(val_dates)]

        logger.info(
            "Data split: train=%d, val=%d dates",
            len(train_dates),
            len(val_dates),
        )

        fwd_ret_cache: dict[int, pd.DataFrame] = {}
        for fd in self.config.forward_days_options:
            fwd_ret_cache[fd] = close.shift(-fd) / close - 1.0

        def _build_xy(data_df: pd.DataFrame, fd: int) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
            fwd = fwd_ret_cache[fd]
            ret_long = fwd.stack(dropna=False).reset_index()
            ret_long.columns = ["date", "symbol", "target"]
            merged = data_df.merge(ret_long, on=["date", "symbol"], how="inner")
            merged = merged.dropna(subset=["target"])
            fc = [c for c in merged.columns if c not in ("date", "symbol", "target")]
            X_m = merged[fc].replace([np.inf, -np.inf], np.nan)
            y_m = merged["target"]
            d_m = merged["date"]
            return X_m, y_m, d_m

        trial_history: list[dict[str, Any]] = []

        def objective(trial: Any) -> float:
            forward_days = trial.suggest_categorical(
                "forward_days", list(self.config.forward_days_options)
            )

            lgbm_params = {
                "n_estimators": trial.suggest_int(
                    "n_estimators",
                    self.config.lgbm_n_estimators_range[0],
                    self.config.lgbm_n_estimators_range[1],
                    step=50,
                ),
                "max_depth": trial.suggest_int(
                    "max_depth",
                    self.config.lgbm_max_depth_range[0],
                    self.config.lgbm_max_depth_range[1],
                ),
                "num_leaves": trial.suggest_int(
                    "num_leaves",
                    self.config.lgbm_num_leaves_range[0],
                    self.config.lgbm_num_leaves_range[1],
                ),
                "learning_rate": trial.suggest_float(
                    "learning_rate",
                    self.config.lgbm_learning_rate_range[0],
                    self.config.lgbm_learning_rate_range[1],
                    log=True,
                ),
                "min_child_samples": trial.suggest_int(
                    "min_child_samples",
                    self.config.lgbm_min_child_samples_range[0],
                    self.config.lgbm_min_child_samples_range[1],
                    step=5,
                ),
                "subsample": trial.suggest_float(
                    "subsample",
                    self.config.lgbm_subsample_range[0],
                    self.config.lgbm_subsample_range[1],
                ),
                "colsample_bytree": trial.suggest_float(
                    "colsample_bytree",
                    self.config.lgbm_colsample_range[0],
                    self.config.lgbm_colsample_range[1],
                ),
                "reg_alpha": trial.suggest_float(
                    "reg_alpha",
                    self.config.lgbm_reg_alpha_range[0],
                    self.config.lgbm_reg_alpha_range[1],
                ),
                "reg_lambda": trial.suggest_float(
                    "reg_lambda",
                    self.config.lgbm_reg_lambda_range[0],
                    self.config.lgbm_reg_lambda_range[1],
                ),
                "random_state": self.config.random_seed,
            }

            try:
                X_train, y_train, d_train = _build_xy(train_df, forward_days)
                X_val, y_val, d_val = _build_xy(val_df, forward_days)
            except Exception as e:
                logger.warning("Data build failed: %s", e)
                return -999.0

            if len(X_train) < 50 or len(X_val) < 20:
                return -999.0

            model = _build_lgbm(lgbm_params)
            model.fit(
                X_train,
                y_train,
                eval_set=[(X_val, y_val)],
                callbacks=[lgb.early_stopping(20, verbose=False)],
            )

            train_pred = model.predict(X_train)
            val_pred = model.predict(X_val)

            train_ic = _spearmanr_safe(train_pred, y_train.values)
            val_ic = _spearmanr_safe(val_pred, y_val.values)

            train_sharpe = train_ic * np.sqrt(252)
            val_sharpe = val_ic * np.sqrt(252)

            overfit_penalty = 0.0
            if val_sharpe > 0 and train_sharpe > val_sharpe * 2:
                overfit_penalty = self.config.overfit_penalty_weight * (
                    train_sharpe - 2 * val_sharpe
                )

            score = val_sharpe - overfit_penalty

            trial_info = {
                "number": trial.number,
                "forward_days": forward_days,
                "val_sharpe": round(val_sharpe, 4),
                "train_sharpe": round(train_sharpe, 4),
                "score": round(score, 4),
                "params": {k: v for k, v in lgbm_params.items()},
            }
            trial_history.append(trial_info)

            if trial.number % 10 == 0:
                logger.info(
                    "Trial %d: fd=%d, val_sharpe=%.4f, train_sharpe=%.4f, score=%.4f",
                    trial.number,
                    forward_days,
                    val_sharpe,
                    train_sharpe,
                    score,
                )

            return score

        study = optuna.create_study(
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=self.config.random_seed),
            pruner=optuna.pruners.MedianPruner(n_startup_trials=10),
        )

        logger.info(
            "Starting joint optimization: %d trials, timeout=%s",
            self.config.n_optuna_trials,
            self.config.optuna_timeout,
        )

        study.optimize(
            objective,
            n_trials=self.config.n_optuna_trials,
            timeout=self.config.optuna_timeout,
            show_progress_bar=False,
        )

        duration = time.time() - t0

        best = study.best_trial
        best_forward_days = best.params["forward_days"]
        best_lgbm_params = {k: v for k, v in best.params.items() if k != "forward_days"}

        X_train, y_train, d_train = _build_xy(train_df, best_forward_days)
        X_val, y_val, d_val = _build_xy(val_df, best_forward_days)

        model = _build_lgbm(best_lgbm_params)
        model.fit(
            X_train,
            y_train,
            eval_set=[(X_val, y_val)],
            callbacks=[lgb.early_stopping(20, verbose=False)],
        )

        train_pred = model.predict(X_train)
        val_pred = model.predict(X_val)
        train_sharpe = _spearmanr_safe(train_pred, y_train.values) * np.sqrt(252)
        val_sharpe = _spearmanr_safe(val_pred, y_val.values) * np.sqrt(252)
        overfit_ratio = train_sharpe / val_sharpe if abs(val_sharpe) > 1e-8 else 10.0

        feature_names = X_train.columns.tolist()
        importance = model.feature_importances_
        total = importance.sum()
        selected_factors = [
            name
            for name, imp in sorted(
                zip(feature_names, importance), key=lambda x: x[1], reverse=True
            )
            if total > 0 and imp / total > 0.001
        ]

        result = OptimizationResult(
            best_params=best_lgbm_params,
            best_val_sharpe=val_sharpe,
            train_sharpe=train_sharpe,
            overfit_ratio=overfit_ratio,
            selected_factors=selected_factors,
            forward_days=best_forward_days,
            n_trials=len(study.trials),
            duration_seconds=duration,
            trial_history=trial_history,
        )

        logger.info(
            "Optimization complete: val_sharpe=%.4f, train_sharpe=%.4f, "
            "overfit_ratio=%.2f, %d trials, %.1fs",
            val_sharpe,
            train_sharpe,
            overfit_ratio,
            len(study.trials),
            duration,
        )

        return result
