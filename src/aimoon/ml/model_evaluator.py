"""Model evaluation and comparison for ML training.

Provides comprehensive evaluation metrics:
- IC (Spearman rank correlation)
- ICIR (IC Information Ratio)
- Directional accuracy
- RMSE
- Per-regime evaluation
- Model comparison and selection
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from aimoon.ml._training_commons import compute_spearmanr_safe

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelMetrics:
    """Comprehensive model evaluation metrics."""

    ic: float = 0.0
    ic_train: float = 0.0
    icir: float = 0.0
    rmse: float = 0.0
    directional_accuracy: float = 0.0
    n_samples: int = 0
    n_features: int = 0
    overfit_ratio: float = 0.0
    fold_ics: tuple[float, ...] = ()
    regime: str = "all"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ic": round(self.ic, 4),
            "ic_train": round(self.ic_train, 4),
            "icir": round(self.icir, 4),
            "rmse": round(self.rmse, 4),
            "directional_accuracy": round(self.directional_accuracy, 4),
            "n_samples": self.n_samples,
            "n_features": self.n_features,
            "overfit_ratio": round(self.overfit_ratio, 2),
            "regime": self.regime,
        }


def compute_ic(
    predictions: np.ndarray | pd.Series,
    labels: np.ndarray | pd.Series,
) -> float:
    """Compute Spearman rank correlation (Information Coefficient).

    Parameters
    ----------
    predictions : array-like
        Model predictions.
    labels : array-like
        Ground truth labels.

    Returns
    -------
    float
        Spearman correlation coefficient.
    """
    if isinstance(predictions, pd.Series):
        predictions = predictions.values
    if isinstance(labels, pd.Series):
        labels = labels.values

    if np.std(predictions) < 1e-10 or np.std(labels) < 1e-10:
        return 0.0

    return compute_spearmanr_safe(predictions, labels)


def compute_icir(
    predictions: np.ndarray | pd.Series,
    labels: np.ndarray | pd.Series,
) -> tuple[float, float, float]:
    """Compute IC, IC std, and ICIR from a single prediction set.

    For per-fold ICIR, use compute_icir_from_folds instead.

    Returns
    -------
    tuple[float, float, float]
        (mean_ic, std_ic, icir)
    """
    ic = compute_ic(predictions, labels)
    # Single observation: ICIR = IC / 1 (no std available)
    return ic, 0.0, ic


def compute_icir_from_folds(fold_ics: list[float]) -> tuple[float, float, float]:
    """Compute IC mean, std, and ICIR from per-fold IC values.

    Parameters
    ----------
    fold_ics : list[float]
        IC values from each cross-validation fold.

    Returns
    -------
    tuple[float, float, float]
        (mean_ic, std_ic, icir)
    """
    if not fold_ics:
        return 0.0, 0.0, 0.0
    mean_ic = float(np.mean(fold_ics))
    std_ic = float(np.std(fold_ics)) if len(fold_ics) > 1 else 0.0
    icir = mean_ic / std_ic if std_ic > 1e-10 else 0.0
    return mean_ic, std_ic, icir


def compute_rmse(
    predictions: np.ndarray | pd.Series,
    labels: np.ndarray | pd.Series,
) -> float:
    """Compute Root Mean Squared Error.

    Parameters
    ----------
    predictions : array-like
        Model predictions.
    labels : array-like
        Ground truth labels.

    Returns
    -------
    float
        RMSE value.
    """
    if isinstance(predictions, pd.Series):
        predictions = predictions.values
    if isinstance(labels, pd.Series):
        labels = labels.values
    return float(np.sqrt(np.mean((predictions - labels) ** 2)))


def compute_directional_accuracy(
    predictions: np.ndarray | pd.Series,
    labels: np.ndarray | pd.Series,
) -> float:
    """Compute directional accuracy (percentage of correct sign predictions).

    Parameters
    ----------
    predictions : array-like
        Model predictions.
    labels : array-like
        Ground truth labels.

    Returns
    -------
    float
        Directional accuracy in [0, 1].
    """
    if isinstance(predictions, pd.Series):
        predictions = predictions.values
    if isinstance(labels, pd.Series):
        labels = labels.values

    if len(predictions) == 0:
        return 0.0

    pred_sign = np.sign(predictions)
    true_sign = np.sign(labels)
    correct = np.sum(pred_sign == true_sign)
    return float(correct / len(predictions))


def evaluate_model(
    predict_fn: Any,
    X: pd.DataFrame,
    y: pd.Series,
    X_train: pd.DataFrame | None = None,
    y_train: pd.Series | None = None,
    fold_ics: list[float] | None = None,
    regime: str = "all",
) -> ModelMetrics:
    """Comprehensive model evaluation.

    Parameters
    ----------
    predict_fn : callable
        Function that takes X and returns predictions.
    X : pd.DataFrame
        Validation features.
    y : pd.Series
        Validation labels.
    X_train : pd.DataFrame | None
        Training features (for overfit detection).
    y_train : pd.Series | None
        Training labels.
    fold_ics : list[float] | None
        Per-fold IC values from cross-validation.
    regime : str
        Market regime label.

    Returns
    -------
    ModelMetrics
        Comprehensive evaluation metrics.
    """
    preds = predict_fn(X)
    ic = compute_ic(preds, y)
    rmse = compute_rmse(preds, y)
    dir_acc = compute_directional_accuracy(preds, y)

    # Train IC for overfit detection
    ic_train = 0.0
    overfit_ratio = 0.0
    if X_train is not None and y_train is not None:
        train_preds = predict_fn(X_train)
        ic_train = compute_ic(train_preds, y_train)
        overfit_ratio = ic_train / (ic + 1e-10)

    # ICIR from folds
    if fold_ics:
        _, _, icir = compute_icir_from_folds(fold_ics)
    else:
        icir = 0.0

    return ModelMetrics(
        ic=ic,
        ic_train=ic_train,
        icir=icir,
        rmse=rmse,
        directional_accuracy=dir_acc,
        n_samples=len(X),
        n_features=X.shape[1],
        overfit_ratio=overfit_ratio,
        fold_ics=tuple(fold_ics) if fold_ics else (),
        regime=regime,
    )


def compare_models(
    models: dict[str, ModelMetrics],
    primary_metric: str = "ic",
) -> tuple[str, dict[str, ModelMetrics]]:
    """Compare multiple models and select the best.

    Parameters
    ----------
    models : dict[str, ModelMetrics]
        Model name -> metrics.
    primary_metric : str
        Primary metric for comparison ("ic", "icir", "directional_accuracy").

    Returns
    -------
    tuple[str, dict[str, ModelMetrics]]
        (best_model_name, sorted_models_by_metric)
    """
    if not models:
        return "", {}

    def sort_key(item: tuple[str, ModelMetrics]) -> float:
        name, metrics = item
        val = getattr(metrics, primary_metric, 0.0)
        # Penalize overfitting
        if metrics.overfit_ratio > 3.0:
            val *= 0.5
        return val

    sorted_models = dict(sorted(models.items(), key=sort_key, reverse=True))
    best_name = next(iter(sorted_models))

    logger.info(
        "Model comparison (%s):",
        primary_metric,
    )
    for name, metrics in sorted_models.items():
        logger.info(
            "  %s: IC=%.4f, ICIR=%.4f, DirAcc=%.2f%%, Overfit=%.1f",
            name,
            metrics.ic,
            metrics.icir,
            metrics.directional_accuracy * 100,
            metrics.overfit_ratio,
        )
    logger.info("  Best: %s", best_name)

    return best_name, sorted_models


class ModelEvaluator:
    """Unified model evaluation interface.

    Example usage::

        evaluator = ModelEvaluator()
        metrics = evaluator.evaluate(model.predict, X_val, y_val, X_train, y_train)
        print(f"IC: {metrics.ic:.4f}")
    """

    def __init__(self, primary_metric: str = "ic"):
        self._primary_metric = primary_metric
        self._history: list[ModelMetrics] = []

    @property
    def primary_metric(self) -> str:
        return self._primary_metric

    @primary_metric.setter
    def primary_metric(self, value: str) -> None:
        self._primary_metric = value

    def evaluate(
        self,
        predict_fn: Any,
        X: pd.DataFrame,
        y: pd.Series,
        X_train: pd.DataFrame | None = None,
        y_train: pd.Series | None = None,
        fold_ics: list[float] | None = None,
        regime: str = "all",
    ) -> ModelMetrics:
        """Evaluate a model and store the result.

        Parameters
        ----------
        predict_fn : callable
            Prediction function.
        X : pd.DataFrame
            Validation features.
        y : pd.Series
            Validation labels.
        X_train : pd.DataFrame | None
            Training features.
        y_train : pd.Series | None
            Training labels.
        fold_ics : list[float] | None
            Per-fold IC values.
        regime : str
            Market regime label.

        Returns
        -------
        ModelMetrics
        """
        metrics = evaluate_model(predict_fn, X, y, X_train, y_train, fold_ics, regime)
        self._history.append(metrics)
        return metrics

    def compare(
        self,
        models: dict[str, ModelMetrics] | None = None,
    ) -> tuple[str, dict[str, ModelMetrics]]:
        """Compare models from history or provided dict.

        Parameters
        ----------
        models : dict[str, ModelMetrics] | None
            Models to compare. If None, uses evaluation history.

        Returns
        -------
        tuple[str, dict[str, ModelMetrics]]
            (best_model_name, sorted_models)
        """
        if models is None:
            # Build from history with generic names
            models = {f"model_{i}": m for i, m in enumerate(self._history)}
        return compare_models(models, self._primary_metric)

    @property
    def history(self) -> list[ModelMetrics]:
        """All evaluation results."""
        return list(self._history)

    def best_in_history(self) -> ModelMetrics | None:
        """Return the best metrics from history."""
        if not self._history:
            return None
        return max(self._history, key=lambda m: getattr(m, self._primary_metric, 0.0))

    def summary(self) -> str:
        """Return a human-readable summary of all evaluations."""
        if not self._history:
            return "No evaluations yet."

        lines = ["=== Model Evaluation Summary ==="]
        for i, m in enumerate(self._history):
            lines.append(
                f"  [{i}] IC={m.ic:.4f}, ICIR={m.icir:.4f}, "
                f"RMSE={m.rmse:.4f}, DirAcc={m.directional_accuracy:.1%}, "
                f"Overfit={m.overfit_ratio:.1f}, Regime={m.regime}"
            )
        best = self.best_in_history()
        if best:
            lines.append(f"  Best IC: {best.ic:.4f}")
        return "\n".join(lines)
