"""ML model inference -> Signal injection."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
import xgboost as xgb

from aimoon.factors.registry import Registry, get_default_registry
from aimoon.ml.feature_pipeline import extract_features
from aimoon.models import Signal

logger = logging.getLogger(__name__)

_FEATURE_CACHE_DIR = Path(".aimoon_cache") / "ml"


def predict_alpha_signals(
    model: xgb.Booster,
    panel: dict[str, pd.DataFrame],
    registry: Registry | None = None,
    sector_map: dict[str, str] | None = None,
    top_k: int = 0,
) -> dict[str, list[Signal]]:
    """Generate stock Signals from trained XGBoost model.

    Uses the model to predict forward returns from current cross-sectional data,
    then maps predictions to Signal objects with scores based on percentile rank.

    Returns
    -------
    dict[str, list[Signal]]
        code -> [Signal, ...]. Empty dict if inference fails.
    """
    if panel is None or "close" not in panel:
        return {}

    registry = registry or get_default_registry()
    features = extract_features(panel, registry, sector_map=sector_map)

    if features.empty:
        logger.warning("ML predict: no features extracted")
        return {}

    # Ensure feature columns match model expectation
    feature_names_path = _FEATURE_CACHE_DIR / "feature_names.json"
    if not feature_names_path.exists():
        logger.warning("ML predict: feature_names.json not found at %s", feature_names_path)
        return {}

    with open(feature_names_path, encoding="utf-8") as f:
        expected_features = json.load(f)

    missing = set(expected_features) - set(features.columns)
    extra = set(features.columns) - set(expected_features)
    if missing:
        logger.debug(
            "ML predict: %d missing features (filled with 0), %d extra (dropped)",
            len(missing),
            len(extra),
        )
    X = features.reindex(columns=expected_features, fill_value=0.0)

    dmatrix = xgb.DMatrix(X)
    try:
        preds = model.predict(dmatrix)
    except Exception as e:
        logger.warning("ML predict failed: %s", e)
        return {}

    pred_series = pd.Series(preds, index=X.index).dropna()
    if len(pred_series) < 5:
        return {}

    if top_k > 0 and top_k < len(pred_series):
        threshold = pred_series.nlargest(top_k).iloc[-1]
        pred_series = pred_series[pred_series >= threshold]

    ranked = pred_series.rank(pct=True)
    signals_by_code: dict[str, list[Signal]] = {}

    for code in pred_series.index:
        pct = ranked[code]
        sigs: list[Signal] = []

        if pct >= 0.90:
            sigs.append(
                Signal(
                    "ml_alpha_strong",
                    f"ML因子强烈看多({pct:.0%})",
                    +5,
                )
            )
        elif pct >= 0.75:
            sigs.append(
                Signal(
                    "ml_alpha",
                    f"ML因子看多({pct:.0%})",
                    +3,
                )
            )
        elif pct <= 0.10:
            sigs.append(
                Signal(
                    "ml_alpha_bear_strong",
                    f"ML因子强烈看空({pct:.0%})",
                    -5,
                )
            )
        elif pct <= 0.25:
            sigs.append(
                Signal(
                    "ml_alpha_bear",
                    f"ML因子看空({pct:.0%})",
                    -3,
                )
            )

        if sigs:
            signals_by_code[str(code)] = sigs

    return signals_by_code
