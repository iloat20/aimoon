"""Advanced feature selection for ML training.

Provides multiple feature selection strategies and a combined pipeline:
1. Variance filter — remove near-constant features
2. Correlation filter — remove highly correlated redundant features
3. IC ranking — select by Information Coefficient
4. L1 regularization — Lasso-based selection
5. Combined pipeline — variance → correlation → IC → L1
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

logger = logging.getLogger(__name__)


def select_features_variance(
    X: pd.DataFrame,
    min_variance: float = 0.01,
) -> list[str]:
    """Remove near-zero-variance features.

    Parameters
    ----------
    X : pd.DataFrame
        Feature matrix (samples x features).
    min_variance : float
        Minimum variance threshold.

    Returns
    -------
    list[str]
        Feature names with variance >= threshold.
    """
    variances = X.var()
    selected = variances[variances >= min_variance].index.tolist()
    removed = X.shape[1] - len(selected)
    if removed > 0:
        logger.info("Variance filter: removed %d features (var < %.4f)", removed, min_variance)
    return selected


def select_features_correlation(
    X: pd.DataFrame,
    threshold: float = 0.85,
    method: str = "pearson",
) -> list[str]:
    """Remove highly correlated redundant features.

    When two features have correlation > threshold, keep the one with
    higher variance (more informative).

    Parameters
    ----------
    X : pd.DataFrame
        Feature matrix.
    threshold : float
        Maximum allowed correlation between any two features.
    method : str
        Correlation method: "pearson", "spearman", or "kendall".

    Returns
    -------
    list[str]
        Feature names after removing redundant ones.
    """
    if X.shape[1] < 2:
        return X.columns.tolist()

    corr = X.corr(method=method).abs()
    upper = corr.where(np.triu(np.ones(corr.shape), k=1).astype(bool))

    # Find features to drop
    to_drop: set[str] = set()
    variances = X.var()

    for col in upper.columns:
        if col in to_drop:
            continue
        highly_corr = upper.index[upper[col] > threshold].tolist()
        for other in highly_corr:
            if other in to_drop:
                continue
            # Keep the one with higher variance
            if variances.get(col, 0) >= variances.get(other, 0):
                to_drop.add(other)
            else:
                to_drop.add(col)
                break

    selected = [c for c in X.columns if c not in to_drop]
    removed = len(to_drop)
    if removed > 0:
        logger.info("Correlation filter: removed %d features (corr > %.2f)", removed, threshold)
    return selected


def select_features_ic(
    X: pd.DataFrame,
    y: pd.Series,
    top_k: int = 100,
    min_ic: float = 0.05,
    require_positive_ic: bool = True,
) -> list[str]:
    """Select features by Information Coefficient (signed IC).

    使用有符号IC，仅选择与未来收益正相关的特征。
    负IC特征会误导模型预测方向。

    Parameters
    ----------
    X : pd.DataFrame
        Feature matrix.
    y : pd.Series
        Target labels (forward returns).
    top_k : int
        Maximum number of features to keep.
    min_ic : float
        Minimum IC to keep a feature (positive values only).
    require_positive_ic : bool
        If True, only select features with positive IC.

    Returns
    -------
    list[str]
        Selected feature names, sorted by IC descending.
    """
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
                ic_scores[col] = ic  # 保留有符号IC
        except Exception:
            continue

    # 仅选择正IC特征（预测方向正确）
    if require_positive_ic:
        filtered = {k: v for k, v in ic_scores.items() if v >= min_ic}
    else:
        filtered = {k: v for k, v in ic_scores.items() if abs(v) >= min_ic}

    if not filtered:
        if require_positive_ic:
            logger.warning("No features with IC >= %.4f, using top %d", min_ic, top_k)
        filtered = ic_scores

    # Sort by IC descending (positive IC first)
    sorted_features = sorted(filtered.items(), key=lambda x: x[1], reverse=True)
    selected = [name for name, _ in sorted_features[:top_k]]
    logger.info(
        "IC feature selection: %d -> %d features (min_IC=%.4f, positive_ic=%s)",
        len(X.columns),
        len(selected),
        min_ic,
        require_positive_ic,
    )
    return selected


def select_features_l1(
    X: pd.DataFrame,
    y: pd.Series,
    top_k: int = 50,
    C: float = 0.1,
) -> list[str]:
    """Select features using L1 (Lasso) regularization.

    Features with non-zero coefficients are selected.

    Parameters
    ----------
    X : pd.DataFrame
        Feature matrix.
    y : pd.Series
        Target labels.
    top_k : int
        Maximum number of features.
    C : float
        Inverse regularization strength. Smaller = more regularization.

    Returns
    -------
    list[str]
        Selected feature names.
    """
    try:
        from sklearn.linear_model import Lasso
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        logger.warning("sklearn not available, skipping L1 selection")
        return X.columns.tolist()

    common = X.index.intersection(y.index)
    if len(common) < 30:
        return X.columns.tolist()

    X_sub = X.loc[common].fillna(0)
    y_sub = y.loc[common]

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_sub.values)

    lasso = Lasso(alpha=C, max_iter=5000, random_state=42)
    lasso.fit(X_scaled, y_sub.values)

    coef_abs = np.abs(lasso.coef_)
    top_indices = np.argsort(coef_abs)[::-1][:top_k]
    selected = [X.columns[i] for i in top_indices if coef_abs[i] > 1e-8]

    if not selected:
        selected = [X.columns[i] for i in top_indices[:top_k]]

    logger.info("L1 feature selection: %d -> %d features", X.shape[1], len(selected))
    return selected


def select_features_combined(
    X: pd.DataFrame,
    y: pd.Series,
    *,
    max_features: int = 60,
    variance_threshold: float = 0.01,
    correlation_threshold: float = 0.85,
    min_ic: float = 0.003,
    use_l1: bool = False,
) -> list[str]:
    """Combined feature selection pipeline.

    Pipeline order:
    1. Variance filter (remove constant features)
    2. Correlation filter (remove redundant features)
    3. IC ranking (select most predictive features)
    4. Optional L1 regularization

    Parameters
    ----------
    X : pd.DataFrame
        Feature matrix.
    y : pd.Series
        Target labels.
    max_features : int
        Maximum number of features to return.
    variance_threshold : float
        Minimum variance threshold.
    correlation_threshold : float
        Maximum correlation between features.
    min_ic : float
        Minimum absolute IC.
    use_l1 : bool
        Whether to apply L1 regularization after IC selection.

    Returns
    -------
    list[str]
        Selected feature names.
    """
    n_start = X.shape[1]

    # Step 1: Variance filter
    selected = select_features_variance(X, min_variance=variance_threshold)
    X_filtered = X[selected]

    # Step 2: Correlation filter
    selected = select_features_correlation(X_filtered, threshold=correlation_threshold)
    X_filtered = X_filtered[selected]

    # Step 3: IC ranking
    selected = select_features_ic(X_filtered, y, top_k=max_features, min_ic=min_ic)
    X_filtered = X[selected]

    # Step 4: Optional L1
    if use_l1 and len(selected) > max_features:
        selected = select_features_l1(X_filtered, y, top_k=max_features)

    logger.info("Combined feature selection: %d -> %d features", n_start, len(selected))
    return selected


def remove_duplicate_features(
    X: pd.DataFrame,
    tolerance: float = 1e-6,
) -> list[str]:
    """Remove exact duplicate features (identical values across all samples).

    Parameters
    ----------
    X : pd.DataFrame
        Feature matrix.
    tolerance : float
        Tolerance for considering two features as identical.

    Returns
    -------
    list[str]
        Unique feature names.
    """
    # Transpose and drop duplicates
    T = X.T
    mask = ~T.duplicated()
    selected = T[mask].index.tolist()
    removed = X.shape[1] - len(selected)
    if removed > 0:
        logger.info("Duplicate filter: removed %d exact duplicate features", removed)
    return selected


def compute_feature_redundancy_map(
    X: pd.DataFrame,
    threshold: float = 0.85,
) -> dict[str, list[str]]:
    """Compute a map of each feature to its highly correlated neighbors.

    Useful for diagnostics and understanding feature redundancy.

    Parameters
    ----------
    X : pd.DataFrame
        Feature matrix.
    threshold : float
        Correlation threshold.

    Returns
    -------
    dict[str, list[str]]
        feature -> list of highly correlated features.
    """
    corr = X.corr().abs()
    redundancy: dict[str, list[str]] = {}
    for col in X.columns:
        neighbors = corr.index[(corr[col] > threshold) & (corr.index != col)].tolist()
        if neighbors:
            redundancy[col] = neighbors
    return redundancy
