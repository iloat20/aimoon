"""ICIR-based dynamic factor weighting.

Computes rolling Information Coefficient ICIR (IC_mean / IC_std) for each
alpha factor, then uses ICIR-proportional weights to scale signal scores.
Replaces static equal-weighted alpha factor allocation.

Based on: Grinold & Kahn, "Active Portfolio Management"
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from aimoon.factors.registry import Registry, get_default_registry

logger = logging.getLogger(__name__)

_ICIR_CACHE_DIR = Path(".aimoon_cache") / "icir"
_ICIR_CACHE_TTL_HOURS = 72  # 3 days


def compute_factor_ic_series(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    registry: Registry | None = None,
    n_dates: int = 60,
    forward_days: int = 5,
) -> pd.DataFrame:
    """Compute IC time series for each alpha factor.

    For each of the last n_dates trading days, compute the Spearman rank
    correlation between each factor's cross-sectional values and the
    forward N-day returns.

    Returns
    -------
    pd.DataFrame
        index=dates, columns=alpha_id, values=IC.
    """
    registry = registry or get_default_registry()
    close = panel.get("close")
    if close is None or len(close) < n_dates + forward_days + 20:
        return pd.DataFrame()

    # Select evenly spaced dates
    available = close.index[20:].tolist()
    if len(available) < n_dates:
        n_dates = len(available)
    step = max(1, len(available) // n_dates)
    dates = [available[i * step] for i in range(n_dates)]

    alpha_ids = registry.list()
    ic_records: list[dict[str, float]] = []

    # Pre-compute all factor DataFrames once (not per date)
    factor_cache: dict[str, pd.DataFrame] = {}
    for alpha_id in alpha_ids:
        try:
            factor_cache[alpha_id] = registry.compute(alpha_id, panel)
        except Exception:
            continue

    for date in dates:
        # 修复前瞻偏差：使用已实现收益（过去 forward_days 天的收益）
        # 而不是前瞻收益（未来 forward_days 天的收益）
        from aimoon.ml.label_engine import generate_realized_returns

        labels = generate_realized_returns(klines, date, forward_days)
        if len(labels) < 10:
            continue

        record: dict[str, float] = {"date": date}

        for alpha_id, factor_df in factor_cache.items():
            if date in factor_df.index:
                row = factor_df.loc[date]
            else:
                continue

            # Cross-sectional rank correlation
            common = row.dropna().index.intersection(labels.index)
            if len(common) < 10:
                continue

            try:
                ic, _ = spearmanr(row[common].values, labels[common].values)
                if not np.isnan(ic):
                    record[alpha_id] = float(ic)
            except Exception:
                continue

        if len(record) > 1:
            ic_records.append(record)

    if not ic_records:
        return pd.DataFrame()

    ic_df = pd.DataFrame(ic_records).set_index("date")
    return ic_df


def compute_icir_weights(
    ic_df: pd.DataFrame,
    decay_halflife: int = 30,
    min_icir: float = 0.0,
) -> dict[str, float]:
    """Compute ICIR weights from IC time series.

    ICIR = mean(IC) / std(IC), with exponential decay weighting.
    Weights are proportional to max(ICIR, 0), normalized to sum to 1.0.

    Parameters
    ----------
    ic_df : pd.DataFrame
        IC time series from compute_factor_ic_series.
    decay_halflife : int
        Half-life in periods for exponential decay weighting.
    min_icir : float
        Minimum ICIR to include a factor (filters noise).

    Returns
    -------
    dict[str, float]
        alpha_id -> weight (sums to 1.0).
    """
    if ic_df.empty:
        return {}

    # Exponential decay weights (more recent = higher weight)
    n = len(ic_df)
    decay = np.exp(-np.log(2) / decay_halflife * np.arange(n)[::-1])
    decay = decay / decay.sum()

    icir_scores: dict[str, float] = {}
    for col in ic_df.columns:
        ic_series = ic_df[col].dropna()
        if len(ic_series) < 5:
            continue

        # Align decay weights to available data
        aligned_decay = decay[-len(ic_series) :]
        aligned_decay = aligned_decay / aligned_decay.sum()

        ic_mean = float(np.average(ic_series.values, weights=aligned_decay))
        ic_std = float(
            np.sqrt(np.average((ic_series.values - ic_mean) ** 2, weights=aligned_decay))
        )

        if ic_std < 1e-10:
            continue

        icir = ic_mean / ic_std
        if icir > min_icir:
            icir_scores[col] = icir

    if not icir_scores:
        return {}

    # Normalize weights: proportional to ICIR
    total = sum(icir_scores.values())
    if total <= 0:
        return {}

    weights = {k: v / total for k, v in icir_scores.items()}
    logger.info(
        "ICIR weights: %d factors, top=%s (%.3f)",
        len(weights),
        max(weights, key=weights.get),
        max(weights.values()),
    )
    return weights


def load_or_compute_icir(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    registry: Registry | None = None,
    cache_ttl_hours: float = _ICIR_CACHE_TTL_HOURS,
) -> dict[str, float]:
    """Load cached ICIR weights or compute fresh ones."""
    import json
    import time

    cache_file = _ICIR_CACHE_DIR / "weights.json"

    # Try loading cached weights
    if cache_file.exists():
        try:
            with open(cache_file, encoding="utf-8") as f:
                cached = json.load(f)
            age_hours = (time.time() - cached.get("timestamp", 0)) / 3600
            if age_hours < cache_ttl_hours and cached.get("weights"):
                logger.info("Using cached ICIR weights (age=%.1fh)", age_hours)
                return cached["weights"]
        except Exception:
            pass

    # Compute fresh
    ic_df = compute_factor_ic_series(panel, klines, registry)
    if ic_df.empty:
        return {}

    weights = compute_icir_weights(ic_df)

    # Save to cache
    if weights:
        _ICIR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "timestamp": time.time(),
                    "weights": weights,
                    "n_factors": len(weights),
                },
                f,
                indent=2,
            )

    return weights
