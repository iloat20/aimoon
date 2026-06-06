"""Shared training utilities used by XGBoost and LightGBM trainers."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path

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
    from scipy.stats import spearmanr

    preds_val = predict_fn(X_val)
    ic_val, _ = spearmanr(preds_val, y_val)
    ic_val = float(ic_val) if not np.isnan(ic_val) else 0.0

    preds_train = predict_fn(X_train)
    ic_train, _ = spearmanr(preds_train, y_train)
    ic_train = float(ic_train) if not np.isnan(ic_train) else 0.0

    ratio = ic_train / (ic_val + 1e-10)
    if ratio > threshold:
        logger.warning(
            "Potential overfitting: train_IC=%.4f >> val_IC=%.4f (ratio=%.2f)",
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
        ranking = sorted(zip(feature_names, mean_abs_shap), key=lambda x: x[1], reverse=True)
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


def features_compatible(prev_names: list[str] | None, new_names: list[str]) -> bool:
    """Check if two feature sets are compatible for warm-start."""
    if not prev_names:
        return True
    return set(prev_names) == set(new_names)
