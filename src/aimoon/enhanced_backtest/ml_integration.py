"""ML model integration and Alpha signal computation.

Extracted from EnhancedBacktestEngine for modularity.

Contains:
- ML model loading (_init_ml_model)
- Stock scoring (_score_stock)
- ML prediction per date (_get_ml_scores_for_date)
- Feature extraction (_extract_features_cached)
- Fallback scoring (_get_fallback_ml_scores)
- Alpha signal per date (_get_alpha_signals_for_date)
- Alpha signal pre-computation (_compute_alpha_signals)
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from aimoon.models import Signal

logger = logging.getLogger(__name__)


def init_ml_model(
    engine: Any,
    klines: dict[str, pd.DataFrame],
    panel: dict[str, pd.DataFrame] | None = None,
    registry: Any = None,
    factor_cache: dict[str, pd.DataFrame] | None = None,
    cache_dir: str | None = None,
) -> None:
    """Load ML ensemble model and adapt weights."""
    try:
        from aimoon.ml.ensemble import EnsemblePredictor
        predictor = EnsemblePredictor.from_cache(cache_dir)
        if not (predictor.has_xgb or predictor.has_lgbm):
            logger.info("ML model unavailable, skipping ML scoring")
            engine._ml_predictor = None
            return

        if panel is None:
            from aimoon.factors.panel import build_panel
            panel = build_panel(klines)
        if registry is None:
            from aimoon.factors.registry import get_default_registry
            registry = get_default_registry()

        engine._ml_panel = panel
        engine._ml_registry = registry
        engine._ml_predictor = predictor

        if factor_cache is not None:
            engine._factor_cache = factor_cache

        try:
            predictor.adapt_weights(panel or {}, klines, registry=registry, factor_cache=factor_cache or {})
        except (ValueError, KeyError, RuntimeError):
            logger.debug("ML weight adapt failed")

        logger.info("ML model loaded: xgb_w=%.2f, lgbm_w=%.2f", predictor._xgb_weight, predictor._lgbm_weight)
    except Exception as e:
        logger.warning("ML model load failed: %s", e)
        engine._ml_predictor = None


def compute_alpha_signals(
    engine: Any,
    klines: dict[str, pd.DataFrame],
) -> dict | None:
    """Pre-compute Alpha Zoo panel + ICIR weights + factor quality filtering.

    Runs outside the backtest loop using the FULL klines dataset.
    Every consumer must slice to current_date via target_date parameter.
    """
    try:
        from aimoon.factors.panel import build_panel
        from aimoon.factors.registry import get_default_registry
        from aimoon.ml.factor_quality import get_or_compute_filtered_ids

        panel = build_panel(klines)
        if panel is None:
            return None
        registry = get_default_registry()

        filtered_ids = get_or_compute_filtered_ids(panel, klines, registry)

        from aimoon.ml.feature_pipeline import _select_factor_subset
        all_factor_ids = _select_factor_subset(registry, 80)

        from aimoon.enhanced_backtest.helpers import parallel_compute_factors
        factor_cache: dict[str, pd.DataFrame] = {}
        parallel_compute_factors(registry, panel, all_factor_ids, factor_cache)
        engine._factor_cache = factor_cache

        try:
            from aimoon.ml.icir_weighter import load_or_compute_ewma
            engine._icir_weights = load_or_compute_ewma(panel, klines, registry, factor_cache=factor_cache)
        except (ValueError, KeyError, RuntimeError):
            logger.debug("ICIR weight computation failed")

        try:
            from aimoon.ml.factor_decay import get_decayed_factors
            engine._decay_factors = get_decayed_factors()
        except (ValueError, KeyError, RuntimeError):
            logger.debug("Factor decay detection failed")

        engine._filtered_factor_ids = set(filtered_ids)

        if engine.use_ml:
            init_ml_model(
                engine, klines, panel=panel, registry=registry,
                factor_cache=factor_cache, cache_dir=engine.cache_dir,
            )

        return {"panel": panel, "registry": registry}
    except Exception as e:
        logger.warning("Alpha Zoo panel build failed: %s", e)
        return None


def score_stock(
    engine: Any,
    code: str,
    name: str,
    kline: pd.DataFrame,
    ctx: dict | None = None,
    alpha_signals: dict[str, list] | None = None,
    ic_weights: dict[str, float] | None = None,
    ml_scores: dict[str, int] | None = None,
    regime: str | None = None,
    _ti: Any = None,
) -> int | None:
    """Composite score: technical indicators + alpha + ML signals."""
    if len(kline) < 60:
        return None

    try:
        from aimoon.indicators.technical import TechInd
        from aimoon.scoring import collect_signals
        ti = _ti if _ti is not None else TechInd(kline)
        signals = collect_signals(ti, code=code)
    except (ValueError, TypeError, IndexError):
        signals = []

    if alpha_signals and code in alpha_signals:
        signals.extend(alpha_signals[code])

    if ml_scores and code in ml_scores:
        ml_score = ml_scores[code]
        alpha_score = int((ml_score - 50) * 0.1)
        alpha_score = max(-40, min(40, alpha_score))

        if ml_score >= 80:
            desc = f"ml_rank_{ml_score}(strong_buy)"
        elif ml_score >= 60:
            desc = f"ml_rank_{ml_score}(buy)"
        elif ml_score <= 20:
            desc = f"ml_rank_{ml_score}(strong_sell)"
        elif ml_score <= 40:
            desc = f"ml_rank_{ml_score}(sell)"
        else:
            desc = f"ml_rank_{ml_score}(neutral)"

        signals.append(Signal("ml_rank", desc, alpha_score))

    if not signals:
        return None

    from aimoon.scoring import hybrid_score
    from aimoon.scoring.hybrid_scorer import get_regime_config
    config = get_regime_config(regime) if regime else None
    return hybrid_score(signals, config)


def get_ml_scores_for_date(
    engine: Any,
    target_date: pd.Timestamp,
) -> dict[str, int] | None:
    """Get ML ensemble percentile scores for a specific date (0-100)."""
    cache_key = str(target_date)
    if cache_key in engine._ml_score_cache:
        return engine._ml_score_cache[cache_key]

    if engine._ml_predictor is None or engine._ml_panel is None:
        result = get_fallback_ml_scores(engine, target_date)
        if result is not None:
            engine._ml_score_cache[cache_key] = result
        return result

    try:
        features = extract_features_cached(engine, target_date)
        if features is None or features.empty:
            return get_fallback_ml_scores(engine, target_date)

        fn = engine._ml_predictor._feature_names
        if fn:
            features = features.reindex(columns=fn, fill_value=0.0)

        predictions: dict[str, np.ndarray] = {}

        if engine._ml_predictor._xgb is not None:
            try:
                import xgboost as xgb
                predictions["xgb"] = engine._ml_predictor._xgb.predict(xgb.DMatrix(features))
            except (ValueError, TypeError):
                logger.debug("XGB predict failed")

        if engine._ml_predictor._lgbm is not None:
            try:
                predictions["lgbm"] = engine._ml_predictor._lgbm.predict(features)
            except (ValueError, TypeError):
                logger.debug("LGBM predict failed")

        if engine._ml_predictor._en is not None and engine._ml_predictor._en_scaler is not None:
            try:
                fn_en = engine._ml_predictor._feature_names
                features_en = features.reindex(columns=fn_en, fill_value=0.0) if fn_en else features
                en_scaled = engine._ml_predictor._en_scaler.transform(features_en.values)
                predictions["en"] = engine._ml_predictor._en.predict(en_scaled)
            except (ValueError, TypeError):
                logger.debug("EN predict failed")

        if not predictions:
            return get_fallback_ml_scores(engine, target_date)

        weights = []
        for name in predictions:
            if name == "xgb":
                weights.append(engine._ml_predictor._xgb_weight)
            elif name == "lgbm":
                weights.append(engine._ml_predictor._lgbm_weight)
            elif name == "en":
                weights.append(engine._ml_predictor._en_weight)

        total_weight = sum(weights)
        if total_weight <= 0:
            return get_fallback_ml_scores(engine, target_date)

        combined = np.zeros(len(features))
        for i, (name, preds) in enumerate(predictions.items()):
            combined += (weights[i] / total_weight) * preds

        pred_series = pd.Series(combined, index=features.index).dropna()
        if len(pred_series) < 5:
            return get_fallback_ml_scores(engine, target_date)

        ranked = pred_series.rank(pct=True)
        scores = (ranked * 100).round().astype(int).to_dict()
        engine._ml_score_cache[cache_key] = scores
        return scores
    except Exception as e:
        logger.debug("ML predict failed @ %s: %s", target_date, e)
        return get_fallback_ml_scores(engine, target_date)


def extract_features_cached(
    engine: Any,
    target_date: pd.Timestamp,
) -> pd.DataFrame | None:
    """Extract features using the same pipeline as training."""
    try:
        from aimoon.ml.feature_pipeline import extract_features
        registry = getattr(engine, "_ml_registry", None)
        predictor = getattr(engine, "_ml_predictor", None)
        zoo_factor_ids = getattr(predictor, "_zoo_factor_ids", None) if predictor else None
        features = extract_features(engine._ml_panel, registry=registry, target_date=target_date, zoo_factor_ids=zoo_factor_ids)
        if features.empty:
            return None
        fn = predictor._feature_names if predictor else None
        if fn:
            missing = [f for f in fn if f not in features.columns]
            extra = [f for f in features.columns if f not in fn]
            logger.debug("Features @ %s: %d extracted, %d model, %d missing, %d extra", target_date, len(features.columns), len(fn), len(missing), len(extra))
        return features
    except Exception as e:
        logger.debug("Feature extraction failed @ %s: %s", target_date, e)
        return None


def get_fallback_ml_scores(
    engine: Any,
    target_date: pd.Timestamp,
) -> dict[str, int] | None:
    """Fallback: use Alpha Zoo aggregate signals as ML score proxy."""
    from aimoon.scoring import hybrid_score
    alpha_ctx = getattr(engine, "_alpha_signals_ctx", None)
    if alpha_ctx is None:
        return None
    try:
        alpha_sigs = get_alpha_signals_for_date(engine, alpha_ctx, target_date)
        if not alpha_sigs:
            return None
        stock_scores: dict[str, float] = {}
        for code, sigs in alpha_sigs.items():
            if not sigs:
                continue
            stock_scores[code] = float(hybrid_score(sigs))
        if len(stock_scores) < 5:
            return None
        scores_series = pd.Series(stock_scores)
        ranked = scores_series.rank(pct=True)
        return (ranked * 100).round().astype(int).to_dict()
    except Exception as e:
        logger.debug("Fallback ML scores failed: %s", e)
        return None


def get_alpha_signals_for_date(
    engine: Any,
    alpha_ctx: dict,
    target_date: pd.Timestamp,
) -> dict[str, list] | None:
    """Get Alpha Zoo cross-sectional signals at a specific date."""
    try:
        from aimoon.factors.scorer import compute_alpha_signals
        panel = alpha_ctx["panel"]
        registry = alpha_ctx["registry"]
        signals = compute_alpha_signals(
            registry, panel, target_date=target_date,
            icir_weights=getattr(engine, "_icir_weights", None),
            decay_factors=getattr(engine, "_decay_factors", None),
            filter_to_ids=getattr(engine, "_filtered_factor_ids", None),
            factor_cache=getattr(engine, "_factor_cache", None),
        )
        n = sum(1 for v in signals.values() if v)
        if n > 0:
            logger.debug("Alpha Zoo @ %s: %d stocks scored", target_date, n)
        return signals if signals else None
    except Exception as e:
        logger.debug("Alpha Zoo @ %s failed: %s", target_date, e)
        return None
