"""Training data collection and date selection utilities."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from aimoon.factors.registry import Registry
from aimoon.ml.feature_pipeline import (
    _select_factor_subset,
    apply_pca_to_alpha360,
    extract_features,
    select_features_by_ic,
)
from aimoon.ml.label_engine import (
    cross_sectional_standardize,
    generate_reversal_labels,
)

logger = logging.getLogger(__name__)

_MIN_DATE_INTERVAL_DAYS = 5


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

    min_required = (n_dates - 1) * min_interval + 1
    if len(available_dates) < min_required:
        n_dates = max(1, (len(available_dates) - 1) // min_interval + 1)
        logger.info(
            "Adjusted n_dates to %d (min_interval=%d, available=%d)",
            n_dates, min_interval, len(available_dates),
        )

    if n_dates <= 1:
        return [available_dates[len(available_dates) // 2]]

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

    selected_dates = _select_dates_evenly(
        available_dates, n_dates, min_interval=max(forward_days, _MIN_DATE_INTERVAL_DAYS)
    )

    all_features: list[pd.DataFrame] = []
    all_labels: list[pd.Series] = []

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

    if standardize_labels:
        n_stocks_per_date = X.groupby("_date").size().median()
        if n_stocks_per_date >= 200:
            y = cross_sectional_standardize(y, X["_date"])
            logger.info(
                "Applied cross-sectional label standardization (n_stocks=%.0f)",
                n_stocks_per_date,
            )
        else:
            logger.info(
                "Skipped cross-sectional standardization for small universe (n_stocks=%.0f)",
                n_stocks_per_date,
            )

    constant_cols = X.nunique() == 1
    if constant_cols.any():
        X = X.loc[:, ~constant_cols]
        logger.info("Removed %d constant features", constant_cols.sum())

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

        selected = select_features_by_ic(X_early, y_early, top_k=40, min_ic=0.015)
        keep_cols = [c for c in selected if c in X.columns]
        if date_col is not None and "_date" not in keep_cols:
            keep_cols.append("_date")
        X = X[keep_cols]
        logger.info(
            "Stability-based feature selection: %d features retained", len(selected)
        )

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
