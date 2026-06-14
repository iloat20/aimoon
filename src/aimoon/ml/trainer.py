"""Time-series cross-validated XGBoost training."""

from __future__ import annotations

import json
import logging
import time
import warnings
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
    compute_spearmanr_safe,
    log_training_summary,
    save_model_artifacts,
    should_retrain_on_overfit,
    try_warm_start_xgb,
)
from aimoon.ml.feature_pipeline import (
    _select_factor_subset,
    apply_pca_to_alpha360,
    compute_feature_importance,
    extract_features,
    select_features_by_ic,
)
from aimoon.ml.label_engine import (
    cross_sectional_standardize,
    generate_labels,
    generate_reversal_labels,
)
from aimoon.ml.optimized_config import get_xgb_params

logger = logging.getLogger(__name__)

_MODEL_DIR = Path(".aimoon_cache") / "ml"
_MODEL_TTL_DAYS = 7

# H2: 最小日期间隔（交易日），确保CV折间独立性
_MIN_DATE_INTERVAL_DAYS = 5


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
    en_result: TrainingResult
    xgb_weight: float
    lgbm_weight: float
    en_weight: float


def _default_params() -> dict[str, Any]:
    """Return XGBoost hyperparameters from centralized config."""
    return get_xgb_params()


def _select_dates_evenly(
    available_dates: list,
    n_dates: int,
    min_interval: int = _MIN_DATE_INTERVAL_DAYS,
) -> list:
    """Select evenly spaced dates with minimum interval.

    H2 Fix: Ensures adjacent selected dates are at least min_interval apart,
    preventing near-identical feature snapshots that inflate CV scores.

    When data is insufficient, reduces n_dates rather than shrinking interval.
    """
    if not available_dates:
        return []

    # Ensure we have enough room for n_dates with min_interval spacing
    min_required = (n_dates - 1) * min_interval + 1
    if len(available_dates) < min_required:
        # Reduce n_dates to fit available data
        n_dates = max(1, (len(available_dates) - 1) // min_interval + 1)
        logger.info(
            "Adjusted n_dates to %d (min_interval=%d, available=%d)",
            n_dates, min_interval, len(available_dates),
        )

    if n_dates <= 1:
        return [available_dates[len(available_dates) // 2]]

    # Evenly space n_dates across available_dates with guaranteed min_interval
    total_span = len(available_dates) - 1
    ideal_step = total_span / (n_dates - 1)
    step = max(min_interval, ideal_step)

    selected = []
    pos = 0
    for _ in range(n_dates):
        if pos >= len(available_dates):
            break
        selected.append(available_dates[int(pos)])
        pos += step

    return sorted(selected)


def _collect_training_data(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    registry: Registry | None = None,
    n_dates: int = 300,
    forward_days: int = 5,
    sector_map: dict[str, str] | None = None,
    *,
    use_pca: bool = False,
    pca_components: int = 50,
    use_clustering: bool = False,
    n_clusters: int = 30,
    standardize_labels: bool = True,
    zoo_factor_ids: list[str] | None = None,
) -> tuple[pd.DataFrame, pd.Series, dict[str, Any]]:
    """Collect features and labels across multiple dates for training.

    Takes snapshots at n_dates evenly spaced dates from the panel.
    Applies PCA to Alpha360 features and cross-sectional label standardization.

    Returns
    -------
    tuple[pd.DataFrame, pd.Series, dict[str, Any]]
        (features, labels, metadata) where metadata contains pca_object,
        feature_names, and other training artifacts.
    """
    close = panel.get("close")
    if close is None or len(close) < 20:
        return pd.DataFrame(), pd.Series(dtype=float), {}

    available_dates = close.index[65:].tolist()  # Alpha360 needs 60+ rows of lookback
    if len(available_dates) < n_dates:
        n_dates = len(available_dates)
    if n_dates < 1:
        return pd.DataFrame(), pd.Series(dtype=float), {}

    # H2: Use improved date selection with minimum interval
    selected_dates = _select_dates_evenly(
        available_dates, n_dates, min_interval=max(forward_days, _MIN_DATE_INTERVAL_DAYS)
    )

    all_features: list[pd.DataFrame] = []
    all_labels: list[pd.Series] = []

    # Use deterministic factor IDs for train/backtest consistency
    _zoo_factor_ids: list[str] | None = None
    if registry is not None:
        _zoo_factor_ids = _select_factor_subset(registry, 80)
        logger.info("Training factor subset: %d factors", len(_zoo_factor_ids))

    for date in selected_dates:
        features = extract_features(
            panel, registry, target_date=date, sector_map=sector_map,
            zoo_factor_ids=_zoo_factor_ids,
        )
        labels = generate_reversal_labels(klines, date, forward_days, lookback_days=20)

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
        return pd.DataFrame(), pd.Series(dtype=float), {}

    X = pd.concat(all_features, axis=0)
    y = pd.concat(all_labels, axis=0)

    # Cross-sectional standardization: remove market factor per date
    # 对小股票池 (<200只) 禁用，因为std估计噪声会放大
    if standardize_labels:
        n_stocks_per_date = X.groupby("_date").size().median()
        if n_stocks_per_date >= 200:
            y = cross_sectional_standardize(y, X["_date"])
            logger.info("Applied cross-sectional label standardization (n_stocks=%.0f)", n_stocks_per_date)
        else:
            logger.info("Skipped cross-sectional standardization for small universe (n_stocks=%.0f)", n_stocks_per_date)

    constant_cols = X.nunique() == 1
    if constant_cols.any():
        X = X.loc[:, ~constant_cols]
        logger.info("Removed %d constant features", constant_cols.sum())

    # Stability-based feature selection: use first 60% of dates (temporal split)
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

        # H3: Balanced feature selection — retain informative features without noise
        selected = select_features_by_ic(X_early, y_early, top_k=40, min_ic=0.015)
        keep_cols = [c for c in selected if c in X.columns]
        if date_col is not None and "_date" not in keep_cols:
            keep_cols.append("_date")
        X = X[keep_cols]
        logger.info(
            "Stability-based feature selection: %d features retained", len(selected)
        )

    # Apply PCA or clustering to Alpha360 features to reduce collinearity
    pca_object = None
    kmeans_object = None
    if use_clustering:
        from aimoon.ml.feature_pipeline import cluster_alpha360_features

        n_before = X.shape[1] - (1 if "_date" in X.columns else 0)
        X, kmeans_object = cluster_alpha360_features(X, n_clusters=n_clusters)
        n_after = X.shape[1] - (1 if "_date" in X.columns else 0)
        logger.info("Clustering: %d features -> %d super factors", n_before, n_after)
    elif use_pca:
        X, pca_object = apply_pca_to_alpha360(X, n_components=pca_components)

    n_features = X.shape[1] - (1 if "_date" in X.columns else 0)
    logger.info(
        "Training data: %d samples, %d features, %d dates",
        len(X),
        n_features,
        len(all_features),
    )

    metadata = {
        "pca_object": pca_object,
        "kmeans_object": kmeans_object,
        "feature_names": [c for c in X.columns if c != "_date"],
        "standardize_labels": standardize_labels,
        "use_pca": use_pca,
        "use_clustering": use_clustering,
        "n_clusters": n_clusters,
        "zoo_factor_ids": _zoo_factor_ids,
    }
    return X, y, metadata


def _search_ensemble_weights(
    combined_en: pd.Series,
    combined_xgb: pd.Series,
    combined_lgbm: pd.Series,
    combined_labels: pd.Series,
    weight_method: str,
) -> tuple[float, float, float, str]:
    """Grid-search optimal ensemble weights.

    Returns (en_weight, xgb_weight, lgbm_weight, weight_method).
    """
    best_ic = -999.0
    en_weight = 0.33
    xgb_weight = 0.33
    lgbm_weight = 0.34

    for w_en in np.arange(0.0, 1.05, 0.1):
        for w_xgb in np.arange(0.0, 1.05, 0.05):
            for w_lgbm in np.arange(0.0, 1.05, 0.05):
                w_total = w_en + w_xgb + w_lgbm
                if w_total <= 0:
                    continue
                combined = (
                    w_en / w_total * combined_en.values
                    + w_xgb / w_total * combined_xgb.values
                    + w_lgbm / w_total * combined_lgbm.values
                )
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", message=".*constant.*")
                    ic_val, _ = spearmanr(combined, combined_labels.values)
                if not np.isnan(ic_val) and ic_val > best_ic:
                    best_ic = ic_val
                    en_weight = w_en / w_total
                    xgb_weight = w_xgb / w_total
                    lgbm_weight = w_lgbm / w_total

    weight_method = "grid_search"
    logger.info("Grid search best IC: %.4f", best_ic)
    return en_weight, xgb_weight, lgbm_weight, weight_method


def train_model(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    registry: Registry | None = None,
    params: dict[str, Any] | None = None,
    n_dates: int = 300,
    forward_days: int = 5,
    save_dir: str | Path | None = None,
    sector_map: dict[str, str] | None = None,
    warm_start: bool = False,
    zoo_factor_ids: list[str] | None = None,
) -> TrainingResult:
    """Train XGBoost model with time-series cross-validation."""
    registry = registry or get_default_registry()
    t0 = time.time()

    X, y, _ = _collect_training_data(
        panel, klines, registry, n_dates, forward_days, sector_map=sector_map,
        zoo_factor_ids=zoo_factor_ids,
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

    # H4: Clip labels after cross-sectional standardization (z-score units)
    y = y.clip(-3.0, 3.0)

    xgb_params = {**_default_params(), **(params or {})}
    feature_names = X.columns.tolist()
    model_params = {
        k: v
        for k, v in xgb_params.items()
        if k not in ("early_stopping_rounds", "n_estimators")
    }

    from aimoon.ml.purged_tscv import PurgedTimeSeriesSplit

    tscv = PurgedTimeSeriesSplit(
        n_splits=8,
        purge_days=forward_days,
        embargo_days=forward_days * 3,
    )
    cv_scores: list[float] = []
    fold_ics: list[float] = []
    best_cv_score = -999.0
    best_round = 0

    # M2: Use time-series split for final model (consistent with CV)
    if dates_column is not None:
        unique_dates = sorted(dates_column.unique())
        cutoff_idx = max(1, int(len(unique_dates) * 0.8))
        # Ensure cutoff leaves at least 1 date for validation
        cutoff_idx = min(cutoff_idx, len(unique_dates) - 1)
        cutoff_date = unique_dates[cutoff_idx]
        train_mask = dates_column <= cutoff_date
        val_mask = dates_column > cutoff_date
        X_train_final = X[train_mask]
        X_val_final = X[val_mask]
        y_train_final = y[train_mask]
        y_val_final = y[val_mask]
        logger.info(
            "Final model split: train=%d samples (%d dates), val=%d samples (%d dates)",
            len(X_train_final), train_mask.sum(),
            len(X_val_final), val_mask.sum(),
        )
    else:
        # Fallback: simple 80/20 split
        val_size = int(len(X) * 0.2)
        X_train_final, X_val_final = X.iloc[:-val_size], X.iloc[-val_size:]
        y_train_final, y_val_final = y.iloc[:-val_size], y.iloc[-val_size:]

    if len(X_train_final) < 10 or len(X_val_final) < 5:
        raise ValueError(
            f"Final split too small: train={len(X_train_final)}, val={len(X_val_final)}"
        )

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
        fold_ic = compute_spearmanr_safe(fold_preds, y_val)
        fold_ics.append(fold_ic)

        if booster.best_score > best_cv_score:
            best_cv_score = booster.best_score
            best_round = booster.best_iteration

    dtrain_final = xgb.DMatrix(X_train_final, label=y_train_final)

    # Warm start: load previous model for incremental training
    prev_model, warm_divisor, feature_names = try_warm_start_xgb(
        save_dir, feature_names, X, X_train_final, y_train_final, X_val_final, y_val_final,
    )
    # Note: try_warm_start_xgb may update X and feature_names via reindex

    num_rounds = (
        min(best_round + 50, xgb_params["n_estimators"])
        if best_round > 0
        else xgb_params["n_estimators"]
    )
    if prev_model is not None:
        num_rounds = max(num_rounds // warm_divisor, 100)

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
    if should_retrain_on_overfit(prev_model is not None, overfit_ratio, model_name="XGBoost"):
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
        logger.info(
            "Fresh retrain: val_IC=%.04f, overfit_ratio=%.1f", ic, overfit_ratio
        )

    importance = compute_feature_importance(final_model, feature_names)
    shap_top20 = compute_shap_top20(final_model, X_val_final, feature_names)
    _, _, fold_icir = compute_icir(fold_ics)

    train_duration = time.time() - t0
    log_training_summary(
        "XGBoost", ic, ic_train, fold_icir,
        X.shape[0], X.shape[1], train_duration,
    )

    # Save artifacts
    if save_dir is not None:
        save_path = Path(save_dir)
        save_model_artifacts(
            save_path,
            final_model,
            feature_names,
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
            model_filename="xgb_model.json",
            feature_filename="xgb_feature_names.json",
            meta_filename="meta.json",
            model_save_fn=lambda m, p: m.save_model(p),
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
    n_dates: int = 300,
    forward_days: int = 5,
    save_dir: str | Path | None = None,
    sector_map: dict[str, str] | None = None,
    warm_start: bool = True,
    zoo_factor_ids: list[str] | None = None,
) -> EnsembleTrainingResult:
    """Train stacking ensemble: Elastic Net + XGBoost + LightGBM.

    Architecture:
        1. Collect features (PCA for tree models, full for Elastic Net)
        2. Cross-sectional label standardization
        3. Train Elastic Net on full features (handles collinearity natively)
        4. Train XGB and LGBM on PCA-reduced features
        5. Grid-search optimal weights
    """
    from aimoon.ml.lgbm_trainer import train_lgbm_model

    registry = registry or get_default_registry()
    save_path = Path(save_dir or _MODEL_DIR)
    save_path.mkdir(parents=True, exist_ok=True)

    logger.info("=== Training Stacking Ensemble (Elastic Net + XGB + LGBM) ===")

    # Train Elastic Net on full features (handles collinearity)
    en_result = train_elasticnet_model(
        panel,
        klines,
        registry,
        n_dates=n_dates,
        forward_days=forward_days,
        save_dir=save_path,
        sector_map=sector_map,
        zoo_factor_ids=zoo_factor_ids,
    )
    logger.info("Elastic Net: IC=%.04f", en_result.ic)

    # Train XGBoost on PCA-reduced features
    xgb_result = train_model(
        panel,
        klines,
        registry,
        n_dates=n_dates,
        forward_days=forward_days,
        save_dir=save_path,
        sector_map=sector_map,
        warm_start=warm_start,
        zoo_factor_ids=zoo_factor_ids,
    )
    logger.info("XGBoost: IC=%.04f", xgb_result.ic)

    # Train LightGBM on PCA-reduced features
    lgbm_result = train_lgbm_model(
        panel,
        klines,
        registry,
        n_dates=n_dates,
        forward_days=forward_days,
        save_dir=save_path,
        sector_map=sector_map,
        warm_start=warm_start,
        zoo_factor_ids=zoo_factor_ids,
    )
    logger.info("LightGBM: IC=%.04f", lgbm_result.ic)

    # H5 + M4: Softmax IC weighting — smooth dynamic weights adapting to relative model quality
    # Temperature-scaled softmax prevents domination by a single model while rewarding higher IC
    import math as _math
    temperature = 0.02
    ics = [max(0.0, en_result.ic), max(0.0, xgb_result.ic), max(0.0, lgbm_result.ic)]
    if max(ics) > 0:
        exp_scores = [_math.exp(ic / temperature) for ic in ics]
        total_exp = sum(exp_scores)
        en_weight = exp_scores[0] / total_exp
        xgb_weight = exp_scores[1] / total_exp
        lgbm_weight = exp_scores[2] / total_exp
    else:
        en_weight = xgb_weight = lgbm_weight = 1.0 / 3.0
    weight_method = "softmax_ic_temperature_0.02"

    # Normalize weights
    total_w = en_weight + xgb_weight + lgbm_weight
    if total_w > 0:
        en_weight /= total_w
        xgb_weight /= total_w
        lgbm_weight /= total_w

    try:
        close_panel = panel.get("close")
        if close_panel is not None and len(close_panel) > 60:
            val_dates = close_panel.index[-20:].tolist()
            all_val_preds_en: list[pd.Series] = []
            all_val_preds_xgb: list[pd.Series] = []
            all_val_preds_lgbm: list[pd.Series] = []
            all_val_labels: list[pd.Series] = []

            for val_date in val_dates:
                features = extract_features(
                    panel,
                    registry,
                    target_date=val_date,
                    sector_map=sector_map,
                    zoo_factor_ids=zoo_factor_ids,
                )
                if features.empty:
                    continue

                common_labels = generate_labels(
                    klines, val_date, forward_days, forward_days
                )
                common = features.index.intersection(common_labels.index)
                if len(common) < 20:
                    continue

                labels_std = common_labels[common]

                # Elastic Net prediction (on full features)
                try:
                    en_path = save_path / "model.elasticnet.json"
                    if en_path.exists():
                        with open(en_path, encoding="utf-8") as f:
                            en_data = json.load(f)
                        from sklearn.preprocessing import StandardScaler
                        en_scaler = StandardScaler()
                        en_scaler.mean_ = np.array(en_data["scaler_mean"], dtype=np.float64)
                        en_scaler.scale_ = np.array(en_data["scaler_scale"], dtype=np.float64)
                        en_scaler.var_ = np.array(en_data["scaler_var"], dtype=np.float64)
                        en_scaler.n_features_in_ = len(en_data["scaler_mean"])
                        en_feat = features.loc[common]
                        en_feat = en_feat.reindex(
                            columns=en_result.feature_names, fill_value=0.0
                        )
                        en_scaled = en_scaler.transform(en_feat.values)
                        preds_en = pd.Series(
                            en_data["model"].predict(en_scaled),
                            index=common,
                        )
                        all_val_preds_en.append(preds_en)
                except (ValueError, TypeError, KeyError):
                    logger.debug("EN predict failed in CV fold")

                # XGB prediction (on PCA features)
                try:
                    fn = xgb_result.feature_names
                    features_xgb = features.loc[common].reindex(
                        columns=fn, fill_value=0.0
                    )
                    preds_xgb = pd.Series(
                        xgb_result.model.predict(xgb.DMatrix(features_xgb)),
                        index=common,
                    )
                    all_val_preds_xgb.append(preds_xgb)
                except (ValueError, TypeError, KeyError):
                    logger.debug("XGB predict failed in CV fold")

                # LGBM prediction
                try:
                    fn = lgbm_result.feature_names
                    features_lgbm = features.loc[common].reindex(
                        columns=fn, fill_value=0.0
                    )
                    preds_lgbm = pd.Series(
                        lgbm_result.model.predict(features_lgbm.values),
                        index=common,
                    )
                    all_val_preds_lgbm.append(preds_lgbm)
                except (ValueError, TypeError, KeyError):
                    logger.debug("LGBM predict failed in CV fold")

                all_val_labels.append(labels_std)

            # Grid search for optimal weights
            if all_val_labels and all_val_preds_en:
                combined_en = pd.concat(all_val_preds_en)
                combined_xgb = (
                    pd.concat(all_val_preds_xgb) if all_val_preds_xgb else combined_en
                )
                combined_lgbm = (
                    pd.concat(all_val_preds_lgbm) if all_val_preds_lgbm else combined_en
                )
                combined_labels = pd.concat(all_val_labels)

                en_weight, xgb_weight, lgbm_weight, weight_method = _search_ensemble_weights(
                    combined_en, combined_xgb, combined_lgbm, combined_labels,
                    weight_method,
                )
    except Exception as e:
        logger.debug("Grid-search failed, using IC-squared proportional: %s", e)

    with open(save_path / "ensemble_meta.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": time.time(),
                "en_ic": round(en_result.ic, 4),
                "xgb_ic": round(xgb_result.ic, 4),
                "lgbm_ic": round(lgbm_result.ic, 4),
                "en_weight": round(en_weight, 4),
                "xgb_weight": round(xgb_weight, 4),
                "lgbm_weight": round(lgbm_weight, 4),
                "weight_method": weight_method,
                "zoo_factor_ids": zoo_factor_ids,
            },
            f,
            indent=2,
        )

    logger.info(
        "Ensemble weights: EN=%.2f, XGB=%.2f, LGBM=%.2f",
        en_weight,
        xgb_weight,
        lgbm_weight,
    )

    # 保存规范特征名列表（所有模型使用的共特征）
    canonical_features_path = save_path / "canonical_feature_names.json"
    if xgb_result.feature_names:
        with open(canonical_features_path, "w", encoding="utf-8") as f:
            json.dump(list(xgb_result.feature_names), f)
        logger.info(
            "规范特征名已保存: %d 个特征 -> %s",
            len(xgb_result.feature_names),
            canonical_features_path,
        )

    return {
        "en_result": en_result,
        "xgb_result": xgb_result,
        "lgbm_result": lgbm_result,
        "en_weight": en_weight,
        "xgb_weight": xgb_weight,
        "lgbm_weight": lgbm_weight,
    }


def train_elasticnet_model(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    registry: Registry | None = None,
    n_dates: int = 300,
    forward_days: int = 5,
    save_dir: str | Path | None = None,
    sector_map: dict[str, str] | None = None,
    zoo_factor_ids: list[str] | None = None,
) -> TrainingResult:
    """Train Elastic Net model with time-series cross-validation.

    Elastic Net combines L1 (feature selection) + L2 (handling collinearity).
    Ideal for Alpha360's 360 collinear features.
    """
    from sklearn.linear_model import ElasticNet
    from sklearn.preprocessing import StandardScaler

    registry = registry or get_default_registry()
    t0 = time.time()

    X, y, metadata = _collect_training_data(
        panel,
        klines,
        registry,
        n_dates,
        forward_days,
        sector_map=sector_map,
        use_clustering=False,  # Elastic Net handles collinearity natively
        use_pca=False,
        standardize_labels=True,
        zoo_factor_ids=zoo_factor_ids,
    )
    if len(X) < 50 or X.shape[1] < 2:
        raise ValueError(
            f"Insufficient training data: {len(X)} samples, {X.shape[1]} features"
        )

    dates_column = None
    if "_date" in X.columns:
        dates_column = X["_date"].copy()
        X = X.drop(columns=["_date"])

    # H4: Clip labels after standardization (z-score units)
    y = y.clip(-3.0, 3.0)

    feature_names = X.columns.tolist()

    # Standardize features
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X.values)

    # Time-series CV to find best alpha/l1_ratio
    from aimoon.ml.purged_tscv import PurgedTimeSeriesSplit

    tscv = PurgedTimeSeriesSplit(
        n_splits=8,
        purge_days=forward_days,
        embargo_days=forward_days * 3,
    )

    best_ic = -999.0
    best_alpha = 0.1
    best_l1_ratio = 0.5

    X_scaled_with_dates = pd.DataFrame(X_scaled, columns=feature_names)
    if dates_column is not None:
        X_scaled_with_dates["_date"] = dates_column.values

    for alpha in [0.01, 0.05, 0.1, 0.5]:
        for l1_ratio in [0.3, 0.5, 0.7]:
            fold_ic_vals: list[float] = []

            for train_idx, val_idx in tscv.split(
                X_scaled_with_dates,
                date_column="_date" if dates_column is not None else None,
            ):
                if len(train_idx) < 10 or len(val_idx) < 5:
                    continue

                model = ElasticNet(
                    alpha=alpha,
                    l1_ratio=l1_ratio,
                    max_iter=5000,
                    random_state=42,
                )
                model.fit(X_scaled[train_idx], y.values[train_idx])
                preds = model.predict(X_scaled[val_idx])

                with warnings.catch_warnings():
                    warnings.filterwarnings(
                        "ignore", message="An input array is constant"
                    )
                    ic_val, _ = spearmanr(preds, y.values[val_idx])
                if not np.isnan(ic_val):
                    fold_ic_vals.append(float(ic_val))

            if fold_ic_vals:
                mean_ic = float(np.mean(fold_ic_vals))
                if mean_ic > best_ic:
                    best_ic = mean_ic
                    best_alpha = alpha
                    best_l1_ratio = l1_ratio

    logger.info(
        "Elastic Net CV: best_alpha=%.3f, best_l1_ratio=%.1f, IC=%.4f",
        best_alpha,
        best_l1_ratio,
        best_ic,
    )

    # Final model with best params
    final_model = ElasticNet(
        alpha=best_alpha,
        l1_ratio=best_l1_ratio,
        max_iter=5000,
        random_state=42,
    )
    final_model.fit(X_scaled, y.values)

    # OOF IC
    oof_preds = np.zeros_like(y.values)
    for train_idx, val_idx in tscv.split(
        X_scaled_with_dates,
        date_column="_date" if dates_column is not None else None,
    ):
        if len(train_idx) < 10 or len(val_idx) < 5:
            continue
        model = ElasticNet(
            alpha=best_alpha,
            l1_ratio=best_l1_ratio,
            max_iter=5000,
            random_state=42,
        )
        model.fit(X_scaled[train_idx], y.values[train_idx])
        oof_preds[val_idx] = model.predict(X_scaled[val_idx])

    ic = compute_spearmanr_safe(oof_preds, y.values)

    # Feature importance (coefficient magnitude)
    importance = dict(zip(feature_names, np.abs(final_model.coef_)))

    train_duration = time.time() - t0
    logger.info(
        "Elastic Net trained: IC=%.4f, alpha=%.3f, l1_ratio=%.1f, %.1fs",
        ic,
        best_alpha,
        best_l1_ratio,
        train_duration,
    )

    # Save model
    save_path = Path(save_dir or _MODEL_DIR)
    save_path.mkdir(parents=True, exist_ok=True)
    en_params = {
        "coef": final_model.coef_.tolist(),
        "intercept": float(final_model.intercept_),
        "n_features_in": int(final_model.n_features_in_),
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "scaler_var": scaler.var_.tolist(),
    }
    with open(save_path / "model.elasticnet.json", "w", encoding="utf-8") as f:
        json.dump(en_params, f, indent=2)
    with open(save_path / "elasticnet_feature_names.json", "w", encoding="utf-8") as f:
        json.dump(feature_names, f)
    with open(save_path / "elasticnet_meta.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": time.time(),
                "ic": round(ic, 4),
                "alpha": best_alpha,
                "l1_ratio": best_l1_ratio,
                "n_stocks": len(y),
                "n_features": X.shape[1],
                "n_dates": n_dates,
                "train_duration": train_duration,
            },
            f,
            indent=2,
        )

    return TrainingResult(
        model=final_model,
        feature_names=tuple(feature_names),
        feature_importance=importance,
        ic=ic,
        n_stocks=len(y),
        n_dates=n_dates,
        train_duration=train_duration,
    )


def ensure_model_fresh(
    klines: dict[str, pd.DataFrame],
    force: bool = False,
    model_dir: str | Path | None = None,
    n_dates: int = 300,
    forward_days: int = 5,
) -> object | None:
    """Check if ensemble needs retraining. Returns EnsemblePredictor or None."""
    from aimoon.ml.ensemble import EnsemblePredictor

    save_dir = Path(model_dir or _MODEL_DIR)
    meta_file = save_dir / "meta.json"
    model_file = save_dir / "xgb_model.json"
    lgbm_file = save_dir / "lgbm_model.txt"

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
                logger.info(
                    "Model stale (%.0f days > %d), retraining",
                    age_days,
                    _MODEL_TTL_DAYS,
                )
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



