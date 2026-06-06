"""Extract feature matrix from Alpha Zoo panel for ML training/inference."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from aimoon.factors.registry import Registry
from aimoon.performance import (
    batch_compute_factors,
    force_gc,
    optimize_factor_dtypes,
)

# Representative Alpha Zoo factors for training (speed vs coverage trade-off)
_MAX_ALPHA_ZOO_FACTORS = 50

logger = logging.getLogger(__name__)


def _robust_zscore(row: pd.Series, clip: float = 3.0) -> pd.Series:
    """Cross-sectional robust z-score: (value - median) / MAD, clipped."""
    median = row.median()
    mad = (row - median).abs().median()
    if mad < 1e-10:
        return row * 0.0
    z = (row - median) / (1.4826 * mad)
    return z.clip(-clip, clip)


def _neutralize_factor(
    factor_row: pd.Series,
    sector_map: dict[str, str] | None = None,
    log_volume: pd.Series | None = None,
) -> pd.Series:
    """Neutralize factor against industry dummies + log(size).

    Barra-style neutralization: regress out industry and size effects,
    return the residual as the neutralized factor value.
    """
    valid = factor_row.dropna()
    if len(valid) < 10:
        return factor_row

    # Build regressors
    regressors: list[pd.Series] = []

    # Industry dummies (one-hot)
    if sector_map:
        sectors = pd.Series({code: sector_map.get(code, "unknown") for code in valid.index})
        dummies = pd.get_dummies(sectors, dtype=float)
        # Drop one column to avoid perfect collinearity
        if len(dummies.columns) > 1:
            dummies = dummies.iloc[:, 1:]
        regressors.append(dummies)

    # Log volume as size proxy
    if log_volume is not None:
        lv = log_volume.reindex(valid.index).fillna(0)
        regressors.append(lv.rename("log_vol"))

    if not regressors:
        return factor_row

    X = pd.concat(regressors, axis=1).fillna(0)
    y = valid

    try:
        # OLS: y = X @ beta + residual
        beta = np.linalg.lstsq(X.values, y.values, rcond=None)[0]
        residual = y - pd.Series(X.values @ beta, index=X.index)
        return _robust_zscore(residual)
    except Exception as e:
        logger.warning("Factor neutralization failed, using raw z-score: %s", e)
        return _robust_zscore(factor_row)


def _select_factor_subset(registry: Registry, max_count: int) -> list[str]:
    """Select a stratified subset of factors across groups for training speed."""
    all_ids = registry.list()
    if len(all_ids) <= max_count:
        return all_ids

    # Group factors by prefix (alpha101, gtja191, qlib158, academic, etc.)
    groups: dict[str, list[str]] = {}
    for fid in all_ids:
        group = fid.rsplit("_", 1)[0] if "_" in fid else fid[:4]
        groups.setdefault(group, []).append(fid)

    # Select evenly across groups
    per_group = max(1, max_count // max(len(groups), 1))
    selected: list[str] = []
    for group_ids in groups.values():
        selected.extend(group_ids[:per_group])
        if len(selected) >= max_count:
            break

    logger.debug(
        "Factor subset: %d -> %d across %d groups",
        len(all_ids),
        len(selected),
        len(groups),
    )
    return selected[:max_count]


def _merge_feature_block(base: pd.DataFrame, addition: pd.DataFrame, label: str) -> pd.DataFrame:
    """Merge a feature block into the base, aligning on common codes."""
    if addition.empty:
        return base
    common = base.index.intersection(addition.index)
    if len(common) < 5:
        return base
    aligned = addition.loc[common]
    result = base.loc[common]
    result = pd.concat([result, aligned], axis=1)
    logger.info("%s merged: +%d features (%d total)", label, aligned.shape[1], result.shape[1])
    return result


def _neutralize_zoo_batch(
    zoo_features: pd.DataFrame,
    panel: dict[str, pd.DataFrame],
    target_date: pd.Timestamp,
    sector_map: dict[str, str],
) -> pd.DataFrame:
    """Neutralize all Alpha Zoo factors against industry + size in batch."""
    vol_panel = panel.get("volume")
    if vol_panel is None or target_date not in vol_panel.index:
        return zoo_features

    log_vol = np.log1p(vol_panel.loc[target_date].clip(lower=1))
    # Build regressor matrix once, then solve all factors as one matrix op
    common_codes = zoo_features.dropna(how="all").index
    if len(common_codes) < 10:
        return zoo_features

    sectors = pd.Series({code: sector_map.get(code, "unknown") for code in common_codes})
    dummies = pd.get_dummies(sectors, dtype=float)
    if len(dummies.columns) > 1:
        dummies = dummies.iloc[:, 1:]
    lv = log_vol.reindex(common_codes).fillna(0)
    X_reg = pd.concat([dummies, lv.rename("log_vol")], axis=1).fillna(0)

    # Batch solve: pinv once, apply to all factor columns
    pinv = np.linalg.pinv(X_reg.values)
    zoo_vals = zoo_features.reindex(common_codes).fillna(0).values
    residuals = zoo_vals - X_reg.values @ (pinv @ zoo_vals)
    neutralized = pd.DataFrame(residuals, index=common_codes, columns=zoo_features.columns)
    # Apply robust z-score column-wise
    for col in neutralized.columns:
        neutralized[col] = _robust_zscore(neutralized[col])

    logger.debug("Alpha Zoo batch neutralized: %d factors", len(neutralized.columns))
    return neutralized


def extract_features(
    panel: dict[str, pd.DataFrame],
    registry: Registry | None = None,
    target_date: pd.Timestamp | None = None,
    sector_map: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Extract feature matrix for ML training/inference.

    Only uses Alpha360 time-series features + basic technical features.
    Alpha Zoo cross-sectional factors are handled separately by the scorer
    to avoid double-counting.

    Returns
    -------
    pd.DataFrame
        index=stock codes, columns=feature names. Empty if no valid features.
    """
    if panel is None or "close" not in panel:
        return pd.DataFrame()

    close = panel.get("close")
    codes = list(close.columns)
    if len(codes) < 5:
        return pd.DataFrame()

    feature_dicts: dict[str, dict[str, float]] = {code: {} for code in codes}

    # 1. Basic technical features from panel (multiple windows)
    if close is not None:
        for code in codes:
            if code not in close.columns:
                continue
            s = close[code].dropna()
            if len(s) < 20:
                continue
            ret = s.pct_change().dropna()
            if target_date is not None and target_date in s.index:
                idx = s.index.get_loc(target_date)
            else:
                idx = len(s) - 1
            for window, suffix in [(5, "5d"), (10, "10d"), (20, "20d")]:
                start = max(0, idx - window)
                recent_ret = ret.iloc[start:idx] if idx > 0 else ret.iloc[-window:]
                if len(recent_ret) > 1:
                    feature_dicts[code][f"tech_volatility_{suffix}"] = float(recent_ret.std())
                    feature_dicts[code][f"tech_return_{suffix}"] = float(recent_ret.mean())

    result = pd.DataFrame.from_dict(feature_dicts, orient="index")
    result = result.fillna(result.median())

    # 2. Alpha360 time-series features
    try:
        from aimoon.ml.alpha360 import extract_alpha360_features

        a360 = extract_alpha360_features(panel, target_date=target_date)
        result = _merge_feature_block(result, a360, "Alpha360")
    except Exception as e:
        logger.warning("Alpha360 feature extraction failed: %s", e)

    # Single dtype optimization pass at the end
    result = optimize_factor_dtypes(result)
    force_gc()

    if result.shape[1] < 10:
        logger.warning(
            "Feature count low (%d), some feature blocks may have failed",
            result.shape[1],
        )
    return result


def select_features_by_ic(
    X: pd.DataFrame,
    y: pd.Series,
    top_k: int = 100,
    min_ic: float = 0.003,  # 进一步降低阈值，保留更多潜在有效特征
) -> list[str]:
    """Select features by Information Coefficient (Spearman rank correlation).

    Parameters
    ----------
    X : pd.DataFrame
        Feature matrix.
    y : pd.Series
        Labels.
    top_k : int
        Maximum number of features to keep.
    min_ic : float
        Minimum absolute IC to keep a feature.

    Returns
    -------
    list[str]
        Selected feature names, sorted by |IC| descending.
    """
    from scipy.stats import spearmanr

    common = X.index.intersection(y.index)
    if len(common) < 30:
        return X.columns.tolist()

    X_sub = X.loc[common]
    y_sub = y.loc[common]

    ic_scores: dict[str, float] = {}
    for col in X_sub.columns:
        try:
            ic, _ = spearmanr(X_sub[col].values, y_sub.values)
            if not np.isnan(ic):
                ic_scores[col] = abs(ic)
        except Exception:
            continue

    # Filter by minimum IC
    filtered = {k: v for k, v in ic_scores.items() if v >= min_ic}
    if not filtered:
        # 如果没有特征达到阈值，降低阈值重试
        logger.warning("No features with IC >= %.4f, retrying with lower threshold", min_ic)
        filtered = {k: v for k, v in ic_scores.items() if v >= 0.001}
        if not filtered:
            return X.columns.tolist()[:top_k]

    # Sort by |IC| descending, take top_k
    sorted_features = sorted(filtered.items(), key=lambda x: x[1], reverse=True)
    selected = [name for name, _ in sorted_features[:top_k]]
    logger.info(
        "Feature selection: %d -> %d features (min_IC=%.3f)",
        len(X.columns),
        len(selected),
        min_ic,
    )
    return selected


def compute_feature_importance(
    model: object,
    feature_names: list[str],
) -> dict[str, float]:
    """Extract feature importance from trained XGBoost model.

    Returns
    -------
    dict[str, float]
        Feature name -> importance score (normalized to sum 1.0).
    """
    import xgboost as xgb

    if isinstance(model, xgb.XGBModel):
        importance = model.feature_importances_
    elif isinstance(model, xgb.Booster):
        score_dict = model.get_score(importance_type="gain")
        importance = np.array([score_dict.get(f, 0.0) for f in feature_names])
    else:
        return {}

    total = float(importance.sum())
    if total <= 0:
        return {}

    result: dict[str, float] = {}
    for name, imp in zip(feature_names, importance):
        result[name] = float(imp) / total
    return dict(sorted(result.items(), key=lambda x: x[1], reverse=True))
