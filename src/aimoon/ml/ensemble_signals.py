"""Ensemble prediction to Signal mapping and weight optimization."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from aimoon.models import Signal

logger = logging.getLogger(__name__)


def ensemble_predict_signals(
    predictor: Any,
    panel: dict[str, pd.DataFrame],
    registry: Any = None,
    sector_map: dict[str, str] | None = None,
) -> dict[str, list[Signal]]:
    """Ensemble prediction → Signal mapping.

    Preserves percentile threshold logic based on ensemble continuous predictions.
    """
    pred_series = predictor.predict(panel, registry, sector_map)
    if len(pred_series) < 5:
        return {}

    ranked = pred_series.rank(pct=True)
    signals_by_code: dict[str, list[Signal]] = {}

    for code in pred_series.index:
        pct = ranked[code]
        sigs: list[Signal] = []

        if pct >= 0.90:
            sigs.append(Signal("ml_alpha_strong", f"ML集成强烈看多({pct:.0%})", +5, category="ml"))
        elif pct >= 0.75:
            sigs.append(Signal("ml_alpha", f"ML集成看多({pct:.0%})", +3, category="ml"))
        elif pct <= 0.10:
            sigs.append(
                Signal("ml_alpha_bear_strong", f"ML集成强烈看空({pct:.0%})", -5, category="ml")
            )
        elif pct <= 0.25:
            sigs.append(Signal("ml_alpha_bear", f"ML集成看空({pct:.0%})", -3, category="ml"))

        if sigs:
            signals_by_code[str(code)] = sigs

    return signals_by_code


def compute_optimal_weights(
    xgb_preds: pd.Series,
    lgbm_preds: pd.Series,
    labels: pd.Series,
    min_weight: float = 0.1,
) -> tuple[float, float]:
    """Compute optimal ensemble weights by minimizing MSE against labels.

    Uses a simple grid search over weight combinations to find the
    (w_xgb, w_lgbm) pair that minimizes weighted-MSE against labels.
    Weights are clamped to [min_weight, 1 - min_weight] and sum to 1.

    Args:
        xgb_preds: XGBoost model predictions.
        lgbm_preds: LightGBM model predictions.
        labels: Ground-truth labels (e.g., forward returns).
        min_weight: Minimum weight per model (prevents zero-weight).

    Returns:
        Tuple of (w_xgb, w_lgbm) that sum to 1.0.
    """
    if len(labels) < 10:
        return 0.5, 0.5

    best_mse = float("inf")
    best_w_xgb = 0.5
    for w_xgb_int in range(int(min_weight * 100), int((1 - min_weight) * 100) + 1, 5):
        w_xgb = w_xgb_int / 100.0
        w_lgbm = 1.0 - w_xgb
        combined = w_xgb * xgb_preds.values + w_lgbm * lgbm_preds.values
        mse = float(np.mean((combined - labels.values) ** 2))
        if mse < best_mse:
            best_mse = mse
            best_w_xgb = w_xgb
    return best_w_xgb, 1.0 - best_w_xgb
