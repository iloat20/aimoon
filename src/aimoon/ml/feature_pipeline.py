"""Extract feature matrix from Alpha Zoo panel for ML training/inference."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from aimoon.factors.registry import Registry
from aimoon.performance import (
    force_gc,
    optimize_factor_dtypes,
)

# Representative Alpha Zoo factors for training (speed vs coverage trade-off)
_MAX_ALPHA_ZOO_FACTORS = 120

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
    """Neutralize factor against industry dummies + log(size)."""
    valid = factor_row.dropna()
    if len(valid) < 10:
        return factor_row

    regressors: list[pd.Series] = []

    if sector_map:
        sectors = pd.Series({code: sector_map.get(code, "unknown") for code in valid.index})
        dummies = pd.get_dummies(sectors, dtype=float)
        if len(dummies.columns) > 1:
            dummies = dummies.iloc[:, 1:]
        regressors.append(dummies)

    if log_volume is not None:
        lv = log_volume.reindex(valid.index).fillna(0)
        regressors.append(lv.rename("log_vol"))

    if not regressors:
        return factor_row

    X = pd.concat(regressors, axis=1).fillna(0)
    y = valid

    try:
        beta = np.linalg.lstsq(X.values, y.values, rcond=None)[0]
        residual = y - pd.Series(X.values @ beta, index=X.index)
        return _robust_zscore(residual)
    except Exception as e:
        logger.warning("Factor neutralization failed, using raw z-score: %s", e)
        return _robust_zscore(factor_row)


def _select_factor_subset(registry: Registry, max_count: int) -> list[str]:
    """Select a stratified subset of factors across groups for training speed.

    Uses stable group detection via zoo directory prefix and deterministic
    ordering (sorted), so repeated calls return the same subset.
    """
    all_ids = registry.list()
    if len(all_ids) <= max_count:
        return all_ids

    groups: dict[str, list[str]] = {}
    for fid in all_ids:
        parts = fid.split("_", 1)
        if fid.startswith("alpha") and len(parts) > 1:
            try:
                int(parts[-1])
                group = parts[0]
            except ValueError:
                group = parts[0]
        elif fid.startswith("alpha") and len(fid) > 6:
            group = fid[:6]
        else:
            group = parts[0]
        groups.setdefault(group, []).append(fid)

    for group in groups:
        groups[group].sort()

    per_group = max(1, max_count // max(len(groups), 1))
    selected: list[str] = []
    sorted_groups = sorted(groups.items())
    for group_name, group_ids in sorted_groups:
        selected.extend(group_ids[:per_group])
        if len(selected) >= max_count:
            break

    result = sorted(set(selected))[:max_count]
    logger.debug(
        "Factor subset: %d -> %d across %d groups (stable selection)",
        len(all_ids),
        len(result),
        len(groups),
    )
    return result


def select_factors_by_icir(
    panel: dict[str, pd.DataFrame],
    registry: Registry,
    max_count: int = 120,
    icir_min: float = 0.02,
    icir_threshold: float = 0.3,
) -> list[str]:
    """ICIR-based factor selection: keep only significant factors.

    Computes Rank IC and ICIR for each factor using recent price data,
    retaining only those with |IC| > icir_min and |ICIR| > icir_threshold.

    Falls back to _select_factor_subset if ICIR computation fails.

    Parameters
    ----------
    panel : dict[str, pd.DataFrame]
        Price panel with at least "close".
    registry : Registry
        Factor registry.
    max_count : int
        Maximum number of factors to return.
    icir_min : float
        Minimum |IC| threshold.
    icir_threshold : float
        Minimum |ICIR| threshold.

    Returns
    -------
    list[str]
        Selected factor IDs.
    """
    import numpy as np

    close = panel.get("close")
    if close is None or len(close) < 60:
        logger.debug("Panel too small for ICIR selection, using group subset")
        return _select_factor_subset(registry, max_count)

    try:
        # Use open-to-open forward returns to match label_engine.generate_labels
        # (labels use open[T+1] to open[T+1+forward_days]).
        # Original close.pct_change().shift(-5) computed close-to-close,
        # creating a basis mismatch between IC selection and training labels.
        open_panel = panel.get("open")
        if open_panel is not None:
            future_ret = open_panel.pct_change(5).shift(-5)
        else:
            # Fallback: close-to-close收益与label的open-to-open不一致
            # 仅影响ICIR排序（相对顺序），不直接影响模型训练
            logger.warning(
                "ICIR selection: open panel not available, falling back to close-to-close "
                "returns (basis mismatch with training labels)"
            )
            future_ret = close.pct_change(5).shift(-5)
        n_days = min(90, len(close) - 10)
        if n_days < 30:
            return _select_factor_subset(registry, max_count)

        selected: list[str] = []
        all_ids = registry.list()

        for alpha_id in all_ids:
            try:
                factor_df = registry.compute(alpha_id, panel)
                if factor_df is None or factor_df.empty:
                    continue

                f_slice = factor_df.iloc[-n_days:]
                ret_slice = future_ret.iloc[-n_days:]

                ic_values = []
                for date in f_slice.index:
                    if date not in ret_slice.index:
                        continue
                    f_row = f_slice.loc[date]
                    r_row = ret_slice.loc[date]
                    common = f_row.dropna().index.intersection(r_row.dropna().index)
                    if len(common) < 10:
                        continue
                    f_vals = f_row[common].values.astype(np.float64)
                    r_vals = r_row[common].values.astype(np.float64)
                    f_rank = np.argsort(np.argsort(f_vals))
                    r_rank = np.argsort(np.argsort(r_vals))
                    n = len(f_rank)
                    if n < 3:
                        continue
                    ic = 1.0 - 6.0 * np.sum((f_rank - r_rank) ** 2) / (n * (n**2 - 1))
                    ic_values.append(ic)

                if len(ic_values) < 10:
                    continue

                ic_mean = float(np.mean(ic_values))
                ic_std = float(np.std(ic_values, ddof=1))
                icir = ic_mean / ic_std if ic_std > 1e-10 else 0.0

                if abs(ic_mean) > icir_min and abs(icir) > icir_threshold:
                    selected.append(alpha_id)
            except Exception:
                continue

        if len(selected) < 5:
            logger.debug("ICIR selection too strict (%d), falling back", len(selected))
            return _select_factor_subset(registry, max_count)

        result = sorted(selected)[:max_count]
        logger.info(
            "ICIR factor selection: %d -> %d (IC>%.3f, ICIR>%.3f)",
            len(all_ids),
            len(result),
            icir_min,
            icir_threshold,
        )
        return result

    except Exception as e:
        logger.warning("ICIR selection failed: %s, using group subset", e)
        return _select_factor_subset(registry, max_count)


def _extract_zoo_factors_with_ids(
    panel: dict[str, pd.DataFrame],
    registry: Registry,
    target_date: pd.Timestamp,
    factor_ids: list[str],
) -> pd.DataFrame:
    """Extract specific Alpha Zoo factors by ID list (deterministic, train/backtest consistent)."""
    close = panel.get("close")
    if close is None:
        return pd.DataFrame()

    codes = list(close.columns)
    if len(codes) < 5:
        return pd.DataFrame()

    factor_values: dict[str, dict[str, float]] = {code: {} for code in codes}
    for alpha_id in factor_ids:
        try:
            factor_df = registry.compute(alpha_id, panel)
            if factor_df.empty or target_date not in factor_df.index:
                continue
            row = factor_df.loc[target_date]
            for code in codes:
                if code in row.index:
                    val = row[code]
                    if pd.notna(val) and not (isinstance(val, float) and (val != val)):
                        factor_values[code][alpha_id] = float(val)
        except Exception:
            continue

    result = pd.DataFrame.from_dict(factor_values, orient="index")
    if result.empty:
        return result

    result = result.apply(_robust_zscore, axis=1)
    result = result.fillna(0.0)
    return result


def _extract_zoo_factors(
    panel: dict[str, pd.DataFrame],
    registry: Registry,
    target_date: pd.Timestamp,
    max_factors: int = 80,
) -> pd.DataFrame:
    """Extract top Alpha Zoo cross-sectional factors as features.

    Uses ICIR-based selection when panel has sufficient data,
    otherwise falls back to deterministic group subset.
    """
    if len(panel.get("close", pd.DataFrame())) > 60:
        selected_ids = select_factors_by_icir(panel, registry, max_factors)
    else:
        selected_ids = _select_factor_subset(registry, max_factors)
    return _extract_zoo_factors_with_ids(panel, registry, target_date, selected_ids)


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
    common_codes = zoo_features.dropna(how="all").index
    if len(common_codes) < 10:
        return zoo_features

    sectors = pd.Series({code: sector_map.get(code, "unknown") for code in common_codes})
    dummies = pd.get_dummies(sectors, dtype=float)
    if len(dummies.columns) > 1:
        dummies = dummies.iloc[:, 1:]
    lv = log_vol.reindex(common_codes).fillna(0)
    X_reg = pd.concat([dummies, lv.rename("log_vol")], axis=1).fillna(0)

    pinv = np.linalg.pinv(X_reg.values)
    zoo_vals = zoo_features.reindex(common_codes).fillna(0).values
    residuals = zoo_vals - X_reg.values @ (pinv @ zoo_vals)
    neutralized = pd.DataFrame(residuals, index=common_codes, columns=zoo_features.columns)
    for col in neutralized.columns:
        neutralized[col] = _robust_zscore(neutralized[col])

    logger.debug("Alpha Zoo batch neutralized: %d factors", len(neutralized.columns))
    return neutralized


def orthogonalize_features(features, variance_threshold=0.95):
    """Orthogonalize features via SVD to remove factor collinearity."""
    import numpy as np

    if features.shape[1] < 3:
        return features
    X = features.fillna(0).values
    n_stocks, n_features = X.shape
    if n_stocks < n_features:
        return features
    try:
        U, S, Vt = np.linalg.svd(X, full_matrices=False)
        explained_var = (S**2) / (S**2).sum()
        cumvar = np.cumsum(explained_var)
        n_components = int(np.searchsorted(cumvar, variance_threshold) + 1)
        n_components = max(2, min(n_components, n_features))
        X_reduced = U[:, :n_components] * S[:n_components]
        cols = ["ortho_" + str(i) for i in range(n_components)]
        result = pd.DataFrame(X_reduced, index=features.index, columns=cols)
        logger.info("SVD orthogonalization: %d -> %d components", n_features, n_components)
        return result
    except Exception as e:
        logger.warning("SVD orthogonalization failed: %s", e)
        return features


def extract_features(
    panel: dict[str, pd.DataFrame],
    registry: Registry | None = None,
    target_date: pd.Timestamp | None = None,
    sector_map: dict[str, str] | None = None,
    zoo_factor_ids: list[str] | None = None,
    feature_medians: pd.Series | None = None,
) -> pd.DataFrame:
    """Extract feature matrix for ML training/inference.

    Combines three feature blocks:
    1. Basic technical features (volatility, returns at multiple windows)
    2. Alpha360 time-series features
    3. Alpha Zoo cross-sectional factors

    Parameters
    ----------
    zoo_factor_ids : list[str] | None
        If provided, use these exact factor IDs (deterministic).
        If None, falls back to random subset selection.
    feature_medians : pd.Series | None
        Training-time feature medians for NaN filling during inference.
        When None (training), uses cross-sectional median (safe).
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
    # 推理时使用训练中位数填充NaN（防止推理截面中位数偏差）
    if feature_medians is not None:
        medians = feature_medians.reindex(result.columns, fill_value=0.0)
        result = result.fillna(medians)
    else:
        result = result.fillna(result.median())

    # 2. Alpha360 time-series features
    try:
        from aimoon.ml.alpha360 import extract_alpha360_features

        a360 = extract_alpha360_features(panel, target_date=target_date)
        result = _merge_feature_block(result, a360, "Alpha360")
    except Exception as e:
        logger.warning("Alpha360 feature extraction failed: %s", e)

    # 2b. A-share robust time-series features (winsorized, asymmetric vol, chip, regime)
    try:
        from aimoon.ml.alpha360_robust import extract_robust_features

        robust = extract_robust_features(panel, target_date=target_date)
        result = _merge_feature_block(result, robust, "RobustFeatures")
    except Exception as e:
        logger.warning("Robust feature extraction failed: %s", e)

    # 3. Alpha Zoo cross-sectional factors
    if registry is not None and target_date is not None:
        try:
            if zoo_factor_ids is not None:
                zoo_features = _extract_zoo_factors_with_ids(
                    panel, registry, target_date, zoo_factor_ids
                )
            else:
                zoo_features = _extract_zoo_factors(panel, registry, target_date)
            if not zoo_features.empty:
                if sector_map and zoo_features.shape[1] > 10:
                    zoo_features = _neutralize_zoo_batch(
                        zoo_features, panel, target_date, sector_map
                    )
                if zoo_features.shape[1] > 20:
                    zoo_features = orthogonalize_features(zoo_features, variance_threshold=0.95)
                result = _merge_feature_block(result, zoo_features, "ZooFactors")
        except Exception as e:
            logger.debug("Zoo factor extraction failed: %s", e)

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
    min_ic: float = 0.003,
) -> list[str]:
    """Select features by Information Coefficient (Spearman rank correlation)."""
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

    filtered = {k: v for k, v in ic_scores.items() if v >= min_ic}
    if not filtered:
        logger.warning("No features with IC >= %.4f, retrying with lower threshold", min_ic)
        filtered = {k: v for k, v in ic_scores.items() if v >= 0.001}
        if not filtered:
            return X.columns.tolist()[:top_k]

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
    """Extract feature importance from trained XGBoost model."""
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


def apply_pca_to_alpha360(X, n_components=50):
    """Apply PCA to reduce feature collinearity."""
    from sklearn.decomposition import PCA

    has_date = "_date" in X.columns
    date_col = X["_date"] if has_date else None
    feat = X.drop(columns=["_date"]) if has_date else X

    n_components = min(n_components, feat.shape[1], feat.shape[0])
    pca = PCA(n_components=n_components, random_state=42)
    transformed = pca.fit_transform(feat.values)
    cols = [f"pca_{i}" for i in range(n_components)]
    result = pd.DataFrame(transformed, index=feat.index, columns=cols)
    if date_col is not None:
        result["_date"] = date_col.values
    return result, pca


def cluster_alpha360_features(X, n_clusters=30):
    """Cluster features into super-factors via KMeans on transposed matrix."""
    from sklearn.cluster import KMeans

    has_date = "_date" in X.columns
    date_col = X["_date"] if has_date else None
    feat = X.drop(columns=["_date"]) if has_date else X

    n_clusters = min(n_clusters, feat.shape[1])
    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = kmeans.fit_predict(feat.T.values)
    cluster_ids = sorted(set(labels))
    result = pd.DataFrame(index=feat.index)
    for cid in cluster_ids:
        members = feat.columns[labels == cid]
        result[f"cluster_{cid}"] = feat[members].mean(axis=1)
    if date_col is not None:
        result["_date"] = date_col.values
    return result, kmeans
