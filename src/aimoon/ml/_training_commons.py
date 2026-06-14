"""Shared training utilities used by XGBoost and LightGBM trainers."""

from __future__ import annotations

import json
import logging
import time
import warnings
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def compute_icir(fold_ics: list[float]) -> tuple[float, float, float]:
    """Compute ICIR from per-fold IC values.

    Returns (mean_ic, std_ic, icir).
    """
    if not fold_ics:
        return 0.0, 0.0, 0.0
    mean_ic = float(np.mean(fold_ics))
    std_ic = float(np.std(fold_ics)) if len(fold_ics) > 1 else 0.0
    icir = mean_ic / std_ic if std_ic > 1e-10 else 0.0
    return mean_ic, std_ic, icir


def check_overfit(
    predict_fn: Callable[[pd.DataFrame], np.ndarray],
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_val: pd.DataFrame,
    y_val: pd.Series,
    threshold: float = 3.0,
) -> tuple[float, float, float]:
    """Compute train/val IC and warn if overfitting.

    Returns (val_ic, train_ic, overfit_ratio).
    """

    preds_val = predict_fn(X_val)
    ic_val = compute_spearmanr_safe(preds_val, y_val)

    preds_train = predict_fn(X_train)
    ic_train = compute_spearmanr_safe(preds_train, y_train)

    ratio = ic_train / (ic_val + 1e-10)
    if ratio > threshold:
        logger.info(
            "Overfitting: train_IC=%.4f >> val_IC=%.4f (ratio=%.2f)",
            ic_train,
            ic_val,
            ratio,
        )
    return ic_val, ic_train, ratio


def compute_shap_top20(
    model: object,
    X_val: pd.DataFrame,
    feature_names: list[str] | tuple[str, ...],
    top_k: int = 20,
) -> dict[str, float]:
    """Compute SHAP-based feature importance, returning top-k."""
    try:
        import shap

        # Sample down for speed if validation set is large
        if len(X_val) > 1000:
            X_val = X_val.sample(1000, random_state=42)

        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X_val)
        mean_abs_shap = np.abs(shap_values).mean(axis=0)
        ranking = sorted(
            zip(feature_names, mean_abs_shap), key=lambda x: x[1], reverse=True
        )
        return {name: round(float(val), 6) for name, val in ranking[:top_k]}
    except ImportError:
        logger.debug("shap not installed, skipping SHAP importance")
    except Exception as e:
        logger.debug("SHAP computation failed: %s", e)
    return {}


def save_training_meta(
    save_path: Path,
    *,
    ic: float,
    ic_train: float,
    fold_ics: list[float],
    n_stocks: int,
    n_features: int,
    n_dates: int,
    forward_days: int,
    train_duration: float,
    cv_scores: list[float],
    best_cv_score: float,
    best_iteration: int,
    overfit_ratio: float,
    n_samples_train: int,
    n_samples_val: int,
    shap_top20: dict[str, float],
    filename: str = "meta.json",
) -> None:
    """Save training metadata JSON to disk."""
    fold_ic_mean, fold_ic_std, icir = compute_icir(fold_ics)
    save_path.mkdir(parents=True, exist_ok=True)
    with open(save_path / filename, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": time.time(),
                "ic": round(ic, 4),
                "ic_train": round(ic_train, 4),
                "fold_ics": [round(s, 4) for s in fold_ics],
                "fold_ic_mean": round(fold_ic_mean, 4),
                "fold_ic_std": round(fold_ic_std, 4),
                "icir": round(icir, 4),
                "n_stocks": n_stocks,
                "n_features": n_features,
                "n_dates": n_dates,
                "forward_days": forward_days,
                "train_duration": round(train_duration, 2),
                "cv_scores": [round(s, 4) for s in cv_scores],
                "best_cv_score": round(best_cv_score, 4),
                "best_iteration": best_iteration,
                "overfit_ratio": round(overfit_ratio, 2),
                "n_samples_train": n_samples_train,
                "n_samples_val": n_samples_val,
                "shap_top20": shap_top20,
            },
            f,
            indent=2,
        )


def features_compatible(prev_names: Sequence[str] | None, new_names: list[str]) -> bool:
    """Check if two feature sets are compatible for warm-start."""
    if not prev_names:
        return True
    return set(prev_names) == set(new_names)


def should_retrain_on_overfit(
    prev_model_exists: bool,
    overfit_ratio: float,
    threshold: float = 5.0,
    model_name: str = "model",
) -> bool:
    """判断是否因过拟合需要重新训练。"""
    if not prev_model_exists:
        return False
    if overfit_ratio <= threshold:
        return False
    logger.warning(
        "%s overfit ratio %.1f > %.1f with warm-start, retraining from scratch",
        model_name,
        overfit_ratio,
        threshold,
    )
    return True


def log_training_summary(
    model_name: str,
    ic: float,
    ic_train: float,
    fold_icir: float,
    n_samples: int,
    n_features: int,
    duration: float,
) -> None:
    """统一的训练完成日志。"""
    logger.info(
        "%s trained: val_IC=%.04f, train_IC=%.04f, ICIR=%.04f, "
        "%d samples x %d features, %.1fs",
        model_name,
        ic,
        ic_train,
        fold_icir,
        n_samples,
        n_features,
        duration,
    )


# ── Warm-start helpers ──────────────────────────────────────────────────────


def try_warm_start_xgb(
    save_dir: Path | None,
    feature_names: list[str],
    X: pd.DataFrame,
    X_train_final: pd.DataFrame,
    y_train_final: pd.Series,
    X_val_final: pd.DataFrame,
    y_val_final: pd.Series,
) -> tuple[Any | None, int, list[str]]:
    """尝试 XGBoost warm start。

    返回 (prev_model, num_rounds_divisor, reordered_feature_names)。
    如果 warm start 失败，返回 (None, 1, feature_names)。
    """
    import xgboost as xgb

    if save_dir is None:
        return None, 1, feature_names

    prev_path = Path(save_dir) / "xgb_model.json"
    if not prev_path.exists():
        return None, 1, feature_names

    try:
        prev_model = xgb.Booster()
        prev_model.load_model(str(prev_path))
        prev_features = prev_model.feature_names

        if not features_compatible(prev_features, feature_names):
            logger.info(
                "XGB warm start discarded: feature mismatch (old=%d, new=%d)",
                len(prev_features or []),
                len(feature_names),
            )
            return None, 1, feature_names

        if prev_features and list(prev_features) != feature_names:
            X = X[list(prev_features)]
            X_val_final = X.iloc[len(X) - len(X_val_final) :]
            logger.info("XGB warm start: reordered features to match previous model")
            feature_names = list(prev_features)

        logger.info("XGB warm start: continuing from previous model")
        return prev_model, 3, feature_names

    except Exception as e:
        logger.warning("XGB warm start failed, training from scratch: %s", e)
        return None, 1, feature_names


def try_warm_start_lgbm(
    save_dir: Path | None,
    feature_names: list[str],
    X: pd.DataFrame,
    X_train_final: pd.DataFrame,
    y_train_final: pd.Series,
    X_val_final: pd.DataFrame,
    y_val_final: pd.Series,
) -> tuple[Any | None, int, list[str]]:
    """尝试 LightGBM warm start。

    返回 (prev_booster, num_rounds_divisor, reordered_feature_names)。
    如果 warm start 失败，返回 (None, 1, feature_names)。
    """
    import lightgbm as lgb

    if save_dir is None:
        return None, 1, feature_names

    prev_path = Path(save_dir) / "model.lgbm.txt"
    if not prev_path.exists():
        return None, 1, feature_names

    try:
        prev_booster = lgb.Booster(model_file=str(prev_path))
        prev_features = prev_booster.feature_name()

        if not features_compatible(prev_features, feature_names):
            logger.info(
                "LGBM warm start discarded: feature mismatch (old=%d, new=%d)",
                len(prev_features or []),
                len(feature_names),
            )
            return None, 1, feature_names

        if prev_features and list(prev_features) != feature_names:
            X = X[list(prev_features)]
            X_val_final = X.iloc[len(X) - len(X_val_final) :]
            logger.info("LGBM warm start: reordered features")
            feature_names = list(prev_features)

        logger.info("LGBM warm start: continuing from previous model")
        return prev_booster, 3, feature_names

    except Exception as e:
        logger.warning("LGBM warm start failed, training from scratch: %s", e)
        return None, 1, feature_names


def detect_and_fix_overfit(
    build_model_fn: Callable[..., Any],
    predict_fn: Callable[[pd.DataFrame], np.ndarray],
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_val: pd.DataFrame,
    y_val: pd.Series,
    prev_model_used: bool,
    model_name: str,
    num_rounds: int,
    **fit_kwargs: Any,
) -> tuple[Any, float, float, float]:
    """检测过拟合并在必要时从头重训。

    返回 (final_model, ic, ic_train, overfit_ratio)。
    """
    model = build_model_fn(num_rounds)
    model.fit(X_train, y_train, **fit_kwargs)

    ic, ic_train, overfit_ratio = check_overfit(
        predict_fn, X_train, y_train, X_val, y_val
    )

    if should_retrain_on_overfit(prev_model_used, overfit_ratio, model_name=model_name):
        model = build_model_fn(num_rounds)
        model.fit(X_train, y_train, **fit_kwargs)

        ic, ic_train, overfit_ratio = check_overfit(
            lambda X_data: model.predict(X_data), X_train, y_train, X_val, y_val
        )
        logger.info(
            "%s fresh retrain: val_IC=%.04f, overfit_ratio=%.1f",
            model_name,
            ic,
            overfit_ratio,
        )

    return model, ic, ic_train, overfit_ratio


def run_hyperopt(
    X: pd.DataFrame,
    y: pd.Series,
    dates_column: pd.Series | None,
    forward_days: int,
    *,
    model_type: str = "xgb",
    n_trials: int = 50,
    force: bool = False,
) -> dict[str, Any] | None:
    """统一的 hyperparameter optimization 入口。

    返回 best_params 或 None（optuna 不可用时）。
    """
    try:
        from aimoon.ml.hyperopt import is_optuna_available
        from aimoon.ml.hyperopt import run_hyperopt as _run
    except ImportError:
        return None

    if not is_optuna_available():
        return None

    val_size = max(int(len(X) * 0.2), 30)
    X_train = X.iloc[:-val_size]
    y_train = y.iloc[:-val_size]
    X_val = X.iloc[-val_size:]
    y_val = y.iloc[-val_size:]

    result = _run(
        X_train, y_train, X_val, y_val,
        model_type=model_type,
        n_trials=n_trials,
        force=force,
    )
    if result is None:
        return None
    return result.best_params


def save_model_artifacts(
    save_path: Path,
    model: Any,
    feature_names: list[str],
    ic: float,
    ic_train: float,
    fold_ics: list[float],
    n_stocks: int,
    n_features: int,
    n_dates: int,
    forward_days: int,
    train_duration: float,
    cv_scores: list[float],
    best_cv_score: float,
    best_iteration: int,
    overfit_ratio: float,
    n_samples_train: int,
    n_samples_val: int,
    shap_top20: dict[str, float],
    model_filename: str,
    feature_filename: str,
    meta_filename: str,
    model_save_fn: Callable[..., Any] | None = None,
) -> None:
    """统一的模型 artifact 保存逻辑。

    Parameters
    ----------
    model_save_fn : callable, optional
        自定义模型保存函数，签名 ``(model, path) -> None``。
        如果为 None，尝试用 ``model.save_model`` 或 ``model.booster_.save_model``。
    """
    save_path.mkdir(parents=True, exist_ok=True)

    # Save model
    if model_save_fn is not None:
        model_save_fn(model, str(save_path / model_filename))
    elif hasattr(model, "save_model") and callable(model.save_model):
        model.save_model(str(save_path / model_filename))
    elif hasattr(model, "booster_") and model.booster_ is not None:
        model.booster_.save_model(str(save_path / model_filename))
    else:
        logger.warning("Model has no save method, skipping model save")

    # Save feature names
    with open(save_path / feature_filename, "w", encoding="utf-8") as f:
        json.dump(feature_names, f)

    # Save training meta
    save_training_meta(
        save_path,
        ic=ic,
        ic_train=ic_train,
        fold_ics=fold_ics,
        n_stocks=n_stocks,
        n_features=n_features,
        n_dates=n_dates,
        forward_days=forward_days,
        train_duration=train_duration,
        cv_scores=cv_scores,
        best_cv_score=best_cv_score,
        best_iteration=best_iteration,
        overfit_ratio=overfit_ratio,
        n_samples_train=n_samples_train,
        n_samples_val=n_samples_val,
        shap_top20=shap_top20,
        filename=meta_filename,
    )

def compute_spearmanr_safe(predictions: np.ndarray, actuals: np.ndarray) -> float:
    """Compute Spearman rank correlation, safely handling constant inputs.

    Returns 0.0 when inputs are constant or correlation is NaN.

    Args:
        predictions: Model predictions array.
        actuals: Ground-truth array.

    Returns:
        Spearman correlation coefficient, or 0.0 if undefined.
    """
    from scipy.stats import spearmanr

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="An input array is constant")
        ic, _ = spearmanr(predictions, actuals)
    return float(ic) if not np.isnan(ic) else 0.0
