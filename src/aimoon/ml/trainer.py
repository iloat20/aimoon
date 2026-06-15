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
    compute_icir,
    compute_shap_top20,
    compute_spearmanr_safe,
    log_training_summary,
)
from aimoon.ml.data_collection import _collect_training_data
from aimoon.ml.feature_pipeline import compute_feature_importance, extract_features
from aimoon.ml.label_engine import generate_labels
from aimoon.ml.model_persistence import save_model_artifacts
from aimoon.ml.optimized_config import get_xgb_params
from aimoon.ml.training_loop import run_cv_training

logger = logging.getLogger(__name__)

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
    en_result: TrainingResult
    xgb_weight: float
    lgbm_weight: float
    en_weight: float


def _default_params() -> dict[str, Any]:
    """Return XGBoost hyperparameters from centralized config."""
    return get_xgb_params()


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

    n_stocks = len(X)

    # H4: Clip labels after cross-sectional standardization (z-score units)
    y = y.clip(-3.0, 3.0)

    xgb_params = {**_default_params(), **(params or {})}
    feature_names = (
        [c for c in X.columns if c != "_date"] if "_date" in X.columns
        else X.columns.tolist()
    )

    (
        final_model, cv_scores, fold_ics, best_cv_score, best_round,
        X_train_final, X_val_final, y_train_final, y_val_final,
    ) = run_cv_training(
        X, y, xgb_params, feature_names, forward_days,
        Path(save_dir) if save_dir else None,
    )

    importance = compute_feature_importance(final_model, feature_names)
    shap_top20 = compute_shap_top20(final_model, X_val_final, feature_names)
    _, _, fold_icir = compute_icir(fold_ics)

    # Re-compute IC for logging/saving
    import xgboost as xgb_mod
    ic = compute_spearmanr_safe(
        final_model.predict(xgb_mod.DMatrix(X_val_final)), y_val_final
    )
    ic_train = compute_spearmanr_safe(
        final_model.predict(xgb_mod.DMatrix(X_train_final)), y_train_final
    )
    overfit_ratio = ic_train / (ic + 1e-10)

    train_duration = time.time() - t0
    log_training_summary(
        "XGBoost", ic, ic_train, fold_icir,
        n_stocks, len(feature_names), train_duration,
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
            n_stocks=n_stocks,
            n_features=len(feature_names),
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
        n_stocks=n_stocks,
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
    cache_dir: str | Path = ".aimoon_cache",
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
    save_path = Path(save_dir) if save_dir else Path(cache_dir) / "ml"
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
    cache_dir: str | Path = ".aimoon_cache",
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
    save_path = Path(save_dir) if save_dir else Path(cache_dir) / "ml"
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
    cache_dir: str | Path = ".aimoon_cache",
    n_dates: int = 300,
    forward_days: int = 5,
) -> object | None:
    """Check if ensemble needs retraining. Returns EnsemblePredictor or None."""
    from aimoon.ml.ensemble import EnsemblePredictor

    save_dir = Path(model_dir) if model_dir else Path(cache_dir) / "ml"
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



