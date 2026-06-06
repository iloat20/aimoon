"""Time-series cross-validated XGBoost training."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

import numpy as np
import pandas as pd
import xgboost as xgb
from scipy.stats import spearmanr

from aimoon.factors.panel import build_panel
from aimoon.factors.registry import Registry, get_default_registry
from aimoon.ml._training_commons import (
    check_overfit,
    compute_icir,
    compute_shap_top20,
    features_compatible,
    save_training_meta,
)
from aimoon.ml.feature_pipeline import (
    compute_feature_importance,
    extract_features,
    select_features_by_ic,
)
from aimoon.ml.label_engine import generate_rank_labels
from aimoon.ml.optimized_config import get_xgb_params

logger = logging.getLogger(__name__)

_MODEL_DIR = Path(".aimoon_cache") / "ml"
_MODEL_TTL_DAYS = 7


@dataclass(frozen=True)
class TrainingResult:
    """ML model training result."""

    model: Any = field(repr=False)  # xgboost.Booster
    feature_names: tuple[str, ...]
    feature_importance: dict[str, float]
    ic: float
    n_stocks: int
    n_dates: int
    train_duration: float


class EnsembleTrainingResult(TypedDict):
    """Type definition for ensemble training results."""

    xgb_result: TrainingResult
    lgbm_result: TrainingResult
    xgb_weight: float
    lgbm_weight: float


def _default_params() -> dict[str, Any]:
    """Return XGBoost hyperparameters from centralized config."""
    return get_xgb_params()


def _collect_training_data(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    registry: Registry | None = None,
    n_dates: int = 200,
    forward_days: int = 5,
    sector_map: dict[str, str] | None = None,
) -> tuple[pd.DataFrame, pd.Series]:
    """Collect features and labels across multiple dates for training.

    Takes snapshots at n_dates evenly spaced dates from the panel.
    Default 120 dates (weekly over ~4 years) for sufficient training diversity.
    """
    close = panel.get("close")
    if close is None or len(close) < 20:
        return pd.DataFrame(), pd.Series(dtype=float)

    available_dates = close.index[65:].tolist()  # Alpha360 needs 60+ rows of lookback
    if len(available_dates) < n_dates:
        n_dates = len(available_dates)
    if n_dates < 1:
        return pd.DataFrame(), pd.Series(dtype=float)

    rng = np.random.default_rng(42)
    interval = max(1, len(available_dates) // (n_dates * 5))
    candidate_dates = available_dates[::interval]
    if len(candidate_dates) > n_dates:
        indices = rng.choice(len(candidate_dates), size=n_dates, replace=False)
        selected_dates = sorted([candidate_dates[i] for i in indices])
    else:
        selected_dates = candidate_dates

    all_features: list[pd.DataFrame] = []
    all_labels: list[pd.Series] = []
    purge_days = forward_days

    for date in selected_dates:
        features = extract_features(panel, registry, target_date=date, sector_map=sector_map)
        labels = generate_rank_labels(klines, date, forward_days, purge_days)

        if features.empty or labels.empty:
            continue

        common = features.index.intersection(labels.index)
        if len(common) < 10:
            continue

        features_with_date = features.loc[common].copy()
        features_with_date["_date"] = date
        all_features.append(features_with_date)
        all_labels.append(labels.loc[common])

    if not all_features:
        return pd.DataFrame(), pd.Series(dtype=float)

    X = pd.concat(all_features, axis=0)
    y = pd.concat(all_labels, axis=0)

    constant_cols = X.nunique() == 1
    if constant_cols.any():
        X = X.loc[:, ~constant_cols]
        logger.info("Removed %d constant features", constant_cols.sum())

    # Stability-based feature selection: use first 60% of dates (temporal split)
    # to avoid leaking future label info into feature selection
    if len(X) > 100 and X.shape[1] > 40:
        date_col = X.get("_date")
        if date_col is not None:
            unique_dates = sorted(date_col.unique())
            cutoff_idx = int(len(unique_dates) * 0.6)
            early_dates = set(unique_dates[:cutoff_idx])
            early_mask = date_col.isin(early_dates)
            X_early = X.loc[early_mask].drop(columns=["_date"], errors="ignore")
            y_early = y.loc[early_mask]
        else:
            split_idx = int(len(X) * 0.6)
            X_early = X.iloc[:split_idx].drop(columns=["_date"], errors="ignore")
            y_early = y.iloc[:split_idx]

        selected = select_features_by_ic(X_early, y_early, top_k=40, min_ic=0.01)
        keep_cols = [c for c in selected if c in X.columns]
        if date_col is not None and "_date" not in keep_cols:
            keep_cols.append("_date")
        X = X[keep_cols]
        logger.info("Stability-based feature selection: %d features retained", len(selected))

    n_features = X.shape[1] - (1 if "_date" in X.columns else 0)
    logger.info(
        "Training data: %d samples, %d features, %d dates",
        len(X),
        n_features,
        len(all_features),
    )
    return X, y


def train_model(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    registry: Registry | None = None,
    params: dict[str, Any] | None = None,
    n_dates: int = 200,
    forward_days: int = 5,
    save_dir: str | Path | None = None,
    sector_map: dict[str, str] | None = None,
    warm_start: bool = False,
) -> TrainingResult:
    """Train XGBoost model with time-series cross-validation."""
    registry = registry or get_default_registry()
    t0 = time.time()

    X, y = _collect_training_data(
        panel, klines, registry, n_dates, forward_days, sector_map=sector_map
    )
    if len(X) < 50 or X.shape[1] < 2:
        raise ValueError(
            f"Insufficient training data: {len(X)} samples, {X.shape[1]} features "
            f"(need >=50 samples and >=2 features)"
        )

    dates_column = None
    if "_date" in X.columns:
        dates_column = X["_date"].copy()
        X = X.drop(columns=["_date"])

    xgb_params = {**_default_params(), **(params or {})}
    feature_names = X.columns.tolist()
    model_params = {
        k: v for k, v in xgb_params.items() if k not in ("early_stopping_rounds", "n_estimators")
    }

    from aimoon.ml.purged_tscv import PurgedTimeSeriesSplit

    tscv = PurgedTimeSeriesSplit(
        n_splits=5,
        purge_days=forward_days,
        embargo_days=forward_days,
    )
    cv_scores: list[float] = []
    fold_ics: list[float] = []
    best_cv_score = -999.0
    best_round = 0

    val_size = int(len(X) * 0.2)
    X_train_final, X_val_final = X.iloc[:-val_size], X.iloc[-val_size:]
    y_train_final, y_val_final = y.iloc[:-val_size], y.iloc[-val_size:]

    X_with_dates = X.copy()
    if dates_column is not None:
        X_with_dates["_date"] = dates_column

    for train_idx, val_idx in tscv.split(X_with_dates, date_column="_date"):
        if len(train_idx) < 10 or len(val_idx) < 5:
            continue

        X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]

        dtrain = xgb.DMatrix(X_train, label=y_train)
        dval = xgb.DMatrix(X_val, label=y_val)

        booster = xgb.train(
            model_params,
            dtrain,
            num_boost_round=xgb_params["n_estimators"],
            evals=[(dval, "val")],
            early_stopping_rounds=xgb_params["early_stopping_rounds"],
            verbose_eval=False,
        )
        cv_scores.append(float(booster.best_score))

        fold_preds = booster.predict(dval)
        fold_ic, _ = spearmanr(fold_preds, y_val)
        fold_ics.append(float(fold_ic) if not np.isnan(fold_ic) else 0.0)

        if booster.best_score > best_cv_score:
            best_cv_score = booster.best_score
            best_round = booster.best_iteration

    dtrain_final = xgb.DMatrix(X_train_final, label=y_train_final)

    # Warm start: load previous model for incremental training
    prev_model = None
    if warm_start and save_dir is not None:
        prev_path = Path(save_dir) / "model.json"
        if prev_path.exists():
            try:
                prev_model = xgb.Booster()
                prev_model.load_model(str(prev_path))
                prev_features = prev_model.feature_names
                if not features_compatible(prev_features, feature_names):
                    logger.info(
                        "Warm start discarded: feature mismatch (old=%d, new=%d)",
                        len(prev_features or []),
                        len(feature_names),
                    )
                    prev_model = None
                else:
                    # Reorder features to match previous model's column order
                    if prev_features and list(prev_features) != feature_names:
                        X = X[list(prev_features)]
                        X_train_final = X.iloc[:-val_size]
                        X_val_final = X.iloc[-val_size:]
                        dtrain_final = xgb.DMatrix(X_train_final, label=y_train_final)
                        feature_names = list(prev_features)
                        logger.info("Warm start: reordered features to match previous model")
                    logger.info("Warm start: continuing from previous model")
            except Exception as e:
                logger.warning("Warm start failed, training from scratch: %s", e)
                prev_model = None

    num_rounds = (
        min(best_round + 50, xgb_params["n_estimators"])
        if best_round > 0
        else xgb_params["n_estimators"]
    )
    if prev_model:
        num_rounds = max(num_rounds // 3, 100)

    final_model = xgb.train(
        model_params,
        dtrain_final,
        num_boost_round=num_rounds,
        xgb_model=prev_model,
        verbose_eval=False,
    )

    # Overfit detection: compare train vs val IC
    def _xgb_predict(X_data: pd.DataFrame) -> np.ndarray:
        return final_model.predict(xgb.DMatrix(X_data))

    ic, ic_train, overfit_ratio = check_overfit(
        _xgb_predict,
        X_train_final,
        y_train_final,
        X_val_final,
        y_val_final,
    )

    # Auto-degradation: if warm-start caused severe overfitting, retrain from scratch
    if prev_model is not None and overfit_ratio > 5.0:
        logger.warning(
            "Overfit ratio %.1f > 5.0 with warm-start, retraining from scratch",
            overfit_ratio,
        )
        final_model = xgb.train(
            model_params,
            dtrain_final,
            num_boost_round=min(best_round + 50, xgb_params["n_estimators"]),
            verbose_eval=False,
        )
        ic, ic_train, overfit_ratio = check_overfit(
            lambda X: final_model.predict(xgb.DMatrix(X)),
            X_train_final,
            y_train_final,
            X_val_final,
            y_val_final,
        )
        logger.info("Fresh retrain: val_IC=%.04f, overfit_ratio=%.1f", ic, overfit_ratio)

    importance = compute_feature_importance(final_model, feature_names)
    shap_top20 = compute_shap_top20(final_model, X_val_final, feature_names)
    _, _, fold_icir = compute_icir(fold_ics)

    train_duration = time.time() - t0
    logger.info(
        "Model trained: val_IC=%.04f, train_IC=%.04f, ICIR=%.04f, "
        "%d samples x %d features, %.1fs",
        ic,
        ic_train,
        fold_icir,
        X.shape[0],
        X.shape[1],
        train_duration,
    )

    # Save artifacts
    if save_dir is not None:
        save_path = Path(save_dir)
        save_path.mkdir(parents=True, exist_ok=True)
        final_model.save_model(str(save_path / "model.json"))
        with open(save_path / "feature_names.json", "w", encoding="utf-8") as f:
            json.dump(feature_names, f)
        save_training_meta(
            save_path,
            ic=ic,
            ic_train=ic_train,
            fold_ics=fold_ics,
            n_stocks=len(y),
            n_features=X.shape[1],
            n_dates=n_dates,
            forward_days=forward_days,
            train_duration=train_duration,
            cv_scores=cv_scores,
            best_cv_score=best_cv_score,
            best_iteration=best_round,
            overfit_ratio=overfit_ratio,
            n_samples_train=len(X_train_final),
            n_samples_val=len(X_val_final),
            shap_top20=shap_top20,
        )

    return TrainingResult(
        model=final_model,
        feature_names=tuple(feature_names),
        feature_importance=importance,
        ic=ic,
        n_stocks=len(X),
        n_dates=n_dates,
        train_duration=train_duration,
    )


def train_ensemble(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    registry: Registry | None = None,
    n_dates: int = 200,
    forward_days: int = 5,
    save_dir: str | Path | None = None,
    sector_map: dict[str, str] | None = None,
    warm_start: bool = True,
) -> EnsembleTrainingResult:
    """Train ensemble: XGBoost + LightGBM with grid-search weight optimization."""
    from aimoon.ml.lgbm_trainer import train_lgbm_model

    registry = registry or get_default_registry()
    save_path = Path(save_dir or _MODEL_DIR)
    save_path.mkdir(parents=True, exist_ok=True)

    logger.info("=== Training ensemble: XGBoost + LightGBM ===")

    xgb_result = train_model(
        panel,
        klines,
        registry,
        n_dates=n_dates,
        forward_days=forward_days,
        save_dir=save_path,
        sector_map=sector_map,
        warm_start=warm_start,
    )
    logger.info("XGBoost: IC=%.04f", xgb_result.ic)

    lgbm_result = train_lgbm_model(
        panel,
        klines,
        registry,
        n_dates=n_dates,
        forward_days=forward_days,
        save_dir=save_path,
        sector_map=sector_map,
        warm_start=warm_start,
    )
    logger.info("LightGBM: IC=%.04f", lgbm_result.ic)

    # Grid-search optimal ensemble weights on validation data
    from aimoon.ml.ensemble import compute_optimal_weights

    xgb_weight, lgbm_weight = 0.5, 0.5
    weight_method = "default"
    try:
        close_panel = panel.get("close")
        if close_panel is not None and len(close_panel) > 60:
            val_dates = close_panel.index[-30:].tolist()
            all_val_preds_xgb: list[pd.Series] = []
            all_val_preds_lgbm: list[pd.Series] = []
            all_val_labels: list[pd.Series] = []

            for val_date in val_dates:
                features = extract_features(
                    panel,
                    registry,
                    target_date=val_date,
                    sector_map=sector_map,
                )
                if features.empty:
                    continue
                fn = xgb_result.feature_names
                features = features.reindex(columns=fn, fill_value=0.0)

                preds_xgb = pd.Series(
                    xgb_result.model.predict(xgb.DMatrix(features)),
                    index=features.index,
                )
                preds_lgbm = pd.Series(
                    lgbm_result.model.predict(features),
                    index=features.index,
                )
                labels = generate_rank_labels(klines, val_date, forward_days, forward_days)
                common = features.index.intersection(labels.index)
                if len(common) < 20:
                    continue

                all_val_preds_xgb.append(preds_xgb[common])
                all_val_preds_lgbm.append(preds_lgbm[common])
                all_val_labels.append(labels[common])

            if all_val_preds_xgb:
                combined_xgb = pd.concat(all_val_preds_xgb)
                combined_lgbm = pd.concat(all_val_preds_lgbm)
                combined_labels = pd.concat(all_val_labels)
                xgb_weight, lgbm_weight = compute_optimal_weights(
                    combined_xgb,
                    combined_lgbm,
                    combined_labels,
                )
                weight_method = "grid_search"
    except Exception as e:
        logger.debug("Grid-search weight optimization failed, using IC-proportional: %s", e)
        if xgb_result.ic > 0 and lgbm_result.ic > 0:
            total_ic = xgb_result.ic + lgbm_result.ic
            xgb_weight = xgb_result.ic / total_ic
            lgbm_weight = lgbm_result.ic / total_ic
            weight_method = "ic_proportional"

    with open(save_path / "ensemble_meta.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": time.time(),
                "xgb_ic": round(xgb_result.ic, 4),
                "lgbm_ic": round(lgbm_result.ic, 4),
                "xgb_weight": round(xgb_weight, 4),
                "lgbm_weight": round(lgbm_weight, 4),
                "weight_method": weight_method,
            },
            f,
            indent=2,
        )

    logger.info("Ensemble weights: XGB=%.2f, LGBM=%.2f", xgb_weight, lgbm_weight)

    return {
        "xgb_result": xgb_result,
        "lgbm_result": lgbm_result,
        "xgb_weight": xgb_weight,
        "lgbm_weight": lgbm_weight,
    }


def ensure_model_fresh(
    klines: dict[str, pd.DataFrame],
    force: bool = False,
    model_dir: str | Path | None = None,
    n_dates: int = 200,
    forward_days: int = 5,
) -> object | None:
    """Check if ensemble needs retraining. Returns EnsemblePredictor or None."""
    from aimoon.ml.ensemble import EnsemblePredictor

    save_dir = Path(model_dir or _MODEL_DIR)
    meta_file = save_dir / "meta.json"
    model_file = save_dir / "model.json"
    lgbm_file = save_dir / "model.lgbm.txt"

    xgb_exists = model_file.exists()
    lgbm_exists = lgbm_file.exists()
    need_train = force or not xgb_exists or not lgbm_exists

    if not need_train and meta_file.exists():
        try:
            with open(meta_file, encoding="utf-8") as f:
                meta = json.load(f)
            age_days = (time.time() - meta.get("timestamp", 0)) / 86400
            if age_days > _MODEL_TTL_DAYS:
                need_train = True
                logger.info("Model stale (%.0f days > %d), retraining", age_days, _MODEL_TTL_DAYS)
        except Exception as e:
            logger.warning("Failed to read model meta: %s", e)
            need_train = True

    if not need_train:
        try:
            predictor = EnsemblePredictor.from_cache(save_dir)
            if predictor.has_xgb or predictor.has_lgbm:
                logger.info("Using cached ensemble model")
                return predictor
        except Exception as e:
            logger.warning("Failed to load cached ensemble: %s", e)
            need_train = True

    if need_train:
        panel = build_panel(klines, min_rows=60)
        if panel is None:
            logger.warning("Cannot train ML model: insufficient panel data")
            return None
        registry = get_default_registry()
        result = train_ensemble(panel, klines, registry, save_dir=save_dir)
        logger.info(
            "Ensemble trained: XGB IC=%.04f, LGBM IC=%.04f",
            result["xgb_result"].ic,
            result["lgbm_result"].ic,
        )
        return EnsemblePredictor.from_cache(save_dir)

    return None
