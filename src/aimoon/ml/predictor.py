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

_DEFAULT_CACHE_DIR = Path(".aimoon_cache") / "ml"


def predict_alpha_signals(
    model: xgb.Booster,
    panel: dict[str, pd.DataFrame],
    registry: Registry | None = None,
    sector_map: dict[str, str] | None = None,
    top_k: int = 0,
    cache_dir: Path = _DEFAULT_CACHE_DIR,
) -> dict[str, list[Signal]]:
    """Generate stock Signals from trained XGBoost model.

    Uses the model to predict forward returns from current cross-sectional data,
    then maps predictions to Signal objects with scores based on percentile rank.

    Parameters
    ----------
    model : xgb.Booster
        Trained XGBoost model.
    panel : dict[str, pd.DataFrame]
        Alpha Zoo panel data.
    registry : Registry | None
        Factor registry.
    sector_map : dict[str, str] | None
        Stock -> sector mapping for neutralization.
    top_k : int
        If > 0, only return top_k stocks by prediction.
    cache_dir : Path
        Cache directory for feature names files.

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
    cache_dir = Path(cache_dir)
    canonical = cache_dir / "canonical_feature_names.json"
    xgb_fn = cache_dir / "xgb_feature_names.json"
    feature_names_path = canonical if canonical.exists() else xgb_fn
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
                    category="ml",
                )
            )
        elif pct >= 0.75:
            sigs.append(
                Signal(
                    "ml_alpha",
                    f"ML因子看多({pct:.0%})",
                    +3,
                    category="ml",
                )
            )
        elif pct <= 0.10:
            sigs.append(
                Signal(
                    "ml_alpha_bear_strong",
                    f"ML因子强烈看空({pct:.0%})",
                    -5,
                    category="ml",
                )
            )
        elif pct <= 0.25:
            sigs.append(
                Signal(
                    "ml_alpha_bear",
                    f"ML因子看空({pct:.0%})",
                    -3,
                    category="ml",
                )
            )

        if sigs:
            signals_by_code[str(code)] = sigs

    return signals_by_code


def predict_with_stacking(panel, registry=None, sector_map=None, top_k=0, cache_dir=None):
    """Generate signals using StackingEnsemble instead of simple weighted average."""
    from aimoon.ml.ensemble import StackingEnsemble

    stacking = StackingEnsemble.load(cache_dir=cache_dir)
    if stacking is None or not stacking.is_fitted:
        return {}
    if panel is None or "close" not in panel:
        return {}
    from aimoon.factors.registry import get_default_registry

    registry = registry or get_default_registry()
    from aimoon.ml.feature_pipeline import extract_features

    features = extract_features(panel, registry, sector_map=sector_map)
    if features.empty:
        return {}
    preds = stacking.predict(features)
    if preds.empty:
        return {}
    ranked = preds.rank(pct=True)
    signals_by_code = {}
    for code in preds.index:
        pct = ranked[code]
        sigs = []
        if pct >= 0.90:
            sigs.append(
                Signal(
                    "stacking_strong",
                    f"Stacking\u5f3a\u70c8\u770b\u591a({pct:.0%})",
                    +5,
                    category="ml",
                )
            )
        elif pct >= 0.75:
            sigs.append(
                Signal("stacking_bull", f"Stacking\u770b\u591a({pct:.0%})", +3, category="ml")
            )
        elif pct <= 0.10:
            sigs.append(
                Signal(
                    "stacking_bear_strong",
                    f"Stacking\u5f3a\u70c8\u770b\u7a7a({pct:.0%})",
                    -5,
                    category="ml",
                )
            )
        elif pct <= 0.25:
            sigs.append(
                Signal("stacking_bear", f"Stacking\u770b\u7a7a({pct:.0%})", -3, category="ml")
            )
        if sigs:
            signals_by_code[str(code)] = sigs
    return signals_by_code
