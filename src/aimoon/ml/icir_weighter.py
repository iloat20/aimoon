"""ICIR-based dynamic factor weighting.

Computes rolling Information Coefficient ICIR (IC_mean / IC_std) for each
alpha factor, then uses ICIR-proportional weights to scale signal scores.
Replaces static equal-weighted alpha factor allocation.

Based on: Grinold & Kahn, "Active Portfolio Management"
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from aimoon.factors.registry import Registry, get_default_registry
from aimoon.ml.label_engine import generate_labels

logger = logging.getLogger(__name__)

_ICIR_CACHE_DIR = Path(".aimoon_cache") / "icir"
_ICIR_CACHE_TTL_HOURS = 72  # 3 days
_EWMA_CACHE_FILE = _ICIR_CACHE_DIR / "ewma_weighter.json"


def compute_factor_ic_series(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    registry: Registry | None = None,
    n_dates: int = 60,
    forward_days: int = 5,
    factor_cache: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Compute IC time series for each alpha factor.

    For each of the last n_dates trading days, compute the Spearman rank
    correlation between each factor's cross-sectional values and the
    forward N-day returns.

    Parameters
    ----------
    factor_cache : dict[str, pd.DataFrame] | None
        Pre-computed factor DataFrames. If provided, skips re-computation.

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

    # Use caller-supplied cache or compute on the fly
    if factor_cache is None:
        factor_cache = {}
        for alpha_id in alpha_ids:
            try:
                factor_cache[alpha_id] = registry.compute(alpha_id, panel)
            except (KeyError, ValueError, RuntimeError):
                continue
            except Exception:
                # SkipAlphaError: factor needs more history than panel provides
                continue
        logger.info(
            "ICIR: cached %d/%d factors in memory (batch compute)",
            len(factor_cache),
            len(alpha_ids),
        )

    # Pre-compute all labels once, reuse across all factors
    all_labels: dict[str, Any] = {}
    for date in dates:
        labels = generate_labels(klines, date, forward_days)
        if len(labels) >= 10:
            all_labels[date] = labels

    for date in dates:
        if date not in all_labels:
            continue
        labels = all_labels[date]

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

            factor_vals = row[common].values
            label_vals = labels[common].values

            # 跳过无方差输入（spearmanr 要求非常量输入）
            if np.std(factor_vals) == 0 or np.std(label_vals) == 0:
                continue

            try:
                ic, _ = spearmanr(factor_vals, label_vals)
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
            np.sqrt(
                np.average((ic_series.values - ic_mean) ** 2, weights=aligned_decay)
            )
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
        max(weights, key=weights.get),  # type: ignore[arg-type]
        max(weights.values()),
    )
    return weights


# ── EWMA Dynamic Factor Weighter ──


class EWMAFactorWeighter:
    """Exponentially weighted ICIR factor weighter.

    Maintains per-factor EWMA of IC mean and IC variance, producing
    adaptive weights that respond faster to regime changes than
    fixed-window ICIR.

    Formulas:
        ewma_mean_new = decay * ewma_mean_old + (1 - decay) * ic_value
        ewma_var_new  = decay * ewma_var_old + (1 - decay) * (ic_value - ewma_mean_old)^2
        icir_ewma     = ewma_mean / sqrt(max(ewma_var, 1e-10))
        weight        = max(icir_ewma, 0), normalized to sum=1

    Parameters
    ----------
    decay : float
        EWMA decay factor (0 < decay < 1). Smaller = faster adaptation.
        0.95 ≈ effective half-life of ~13 observations.
    min_weight : float
        Minimum weight floor (0 = allow zero-weight factors).
    """

    def __init__(self, decay: float = 0.95, min_weight: float = 0.0):
        self._decay = decay
        self._min_weight = min_weight
        self._ewma_mean: dict[str, float] = {}
        self._ewma_var: dict[str, float] = {}
        self._n_updates: int = 0

    @property
    def decay(self) -> float:
        return self._decay

    @property
    def n_updates(self) -> int:
        return self._n_updates

    def update(self, ic_series: dict[str, float] | pd.Series) -> dict[str, float]:
        """Update with new IC observations, return updated weights.

        Parameters
        ----------
        ic_series : dict[str, float] | pd.Series
            Factor ID -> IC value for this observation period.

        Returns
        -------
        dict[str, float]
            Updated normalized weights (factor_id -> weight, sums to 1.0).
        """
        if isinstance(ic_series, pd.Series):
            ic_series = ic_series.dropna().to_dict()

        if not ic_series:
            return self.get_weights()

        d = self._decay
        for factor_id, ic_val in ic_series.items():
            ic = float(ic_val)
            if np.isnan(ic):
                continue

            old_mean = self._ewma_mean.get(factor_id, ic)
            old_var = self._ewma_var.get(factor_id, 0.0)

            # Update EWMA mean
            new_mean = d * old_mean + (1 - d) * ic
            # Update EWMA variance (Welford-style EWMA)
            diff = ic - old_mean
            new_var = d * old_var + (1 - d) * diff * diff

            self._ewma_mean[factor_id] = new_mean
            self._ewma_var[factor_id] = new_var

        self._n_updates += 1
        weights = self.get_weights()

        logger.debug(
            "EWMA update #%d: %d factors, top=%s (%.4f)",
            self._n_updates,
            len(weights),
            max(weights, key=weights.get) if weights else "none",  # type: ignore[arg-type]
            max(weights.values()) if weights else 0.0,
        )
        return weights

    def get_weights(self) -> dict[str, float]:
        """Return current normalized weights.

        Returns
        -------
        dict[str, float]
            factor_id -> weight (sums to 1.0). Empty if no data.
        """
        if not self._ewma_mean:
            return {}

        icir_scores: dict[str, float] = {}
        for factor_id, mean in self._ewma_mean.items():
            var = self._ewma_var.get(factor_id, 0.0)
            # Skip near-zero-variance factors: ICIR would be numerically
            # unstable (mean / tiny_std → huge value) and produce a single
            # dominant weight that crowds out all other factors.
            if var < 1e-8:
                continue
            std = np.sqrt(var)
            icir = mean / std
            if icir > 0:
                icir_scores[factor_id] = icir

        if not icir_scores:
            return {}

        # Winsorize extreme ICIR values to prevent a single factor from
        # dominating the weight distribution.  Cap at the 95th percentile.
        # Only apply when we have enough factors for the percentile to be meaningful.
        icir_values = np.array(list(icir_scores.values()))
        if len(icir_values) > 5:
            p95 = np.percentile(icir_values, 95)
            icir_scores = {k: min(v, p95) for k, v in icir_scores.items()}

        total = sum(icir_scores.values())
        if total <= 0:
            return {}

        weights = {k: v / total for k, v in icir_scores.items()}

        # Apply min_weight floor if configured
        if self._min_weight > 0:
            weights = {k: max(v, self._min_weight) for k, v in weights.items()}
            total = sum(weights.values())
            weights = {k: v / total for k, v in weights.items()}

        return weights

    def get_icir_values(self) -> dict[str, float]:
        """Return raw ICIR values (not normalized) for diagnostics."""
        result: dict[str, float] = {}
        for factor_id, mean in self._ewma_mean.items():
            var = self._ewma_var.get(factor_id, 0.0)
            std = np.sqrt(max(var, 1e-10))
            result[factor_id] = mean / std
        return result

    def save(self, path: Path | None = None) -> None:
        """Persist state to JSON."""
        path = path or _EWMA_CACHE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "timestamp": time.time(),
            "decay": self._decay,
            "min_weight": self._min_weight,
            "n_updates": self._n_updates,
            "ewma_mean": self._ewma_mean,
            "ewma_var": self._ewma_var,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, path: Path | None = None) -> EWMAFactorWeighter | None:
        """Load state from JSON. Returns None if file missing or corrupt."""
        path = path or _EWMA_CACHE_FILE
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            weighter = cls(
                decay=data.get("decay", 0.95),
                min_weight=data.get("min_weight", 0.0),
            )
            weighter._ewma_mean = data.get("ewma_mean", {})
            weighter._ewma_var = data.get("ewma_var", {})
            weighter._n_updates = data.get("n_updates", 0)
            return weighter
        except Exception as e:
            logger.debug("Failed to load EWMA weighter: %s", e)
            return None


def load_or_compute_ewma(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    registry: Registry | None = None,
    decay: float = 0.95,
    cache_ttl_hours: float = _ICIR_CACHE_TTL_HOURS,
    factor_cache: dict[str, pd.DataFrame] | None = None,
) -> dict[str, float]:
    """Load cached EWMA weighter or bootstrap from IC time series.

    On first call: computes IC series, feeds all observations into
    EWMAFactorWeighter.update(), caches state.

    On subsequent calls: loads cached state, returns current weights.
    Call ``update()`` periodically (e.g. weekly) to keep weights fresh.

    Parameters
    ----------
    panel : dict[str, pd.DataFrame]
        Alpha Zoo panel data.
    klines : dict[str, pd.DataFrame]
        Stock kline data.
    registry : Registry | None
        Factor registry.
    decay : float
        EWMA decay factor.
    cache_ttl_hours : float
        Cache TTL in hours.

    Returns
    -------
    dict[str, float]
        factor_id -> normalized weight (sums to 1.0).
    """
    # Try loading cached state
    weighter = EWMAFactorWeighter.load(_EWMA_CACHE_FILE)
    if weighter is not None and weighter.n_updates > 0:
        try:
            age_hours = (time.time() - _EWMA_CACHE_FILE.stat().st_mtime) / 3600
            if age_hours < cache_ttl_hours:
                weights = weighter.get_weights()
                if weights:
                    logger.info(
                        "Using cached EWMA weights (age=%.1fh, %d factors, %d updates)",
                        age_hours,
                        len(weights),
                        weighter.n_updates,
                    )
                    return weights
        except Exception:
            pass

    # Bootstrap: compute IC series and feed into EWMA
    logger.info("Bootstrapping EWMA factor weighter...")
    ic_df = compute_factor_ic_series(
        panel,
        klines,
        registry,
        factor_cache=factor_cache,
    )
    if ic_df.empty:
        return {}

    weighter = EWMAFactorWeighter(decay=decay)
    for _, row in ic_df.iterrows():
        ic_obs = row.dropna().to_dict()
        if ic_obs:
            weighter.update(ic_obs)

    weights = weighter.get_weights()
    if weights:
        weighter.save()
        logger.info(
            "EWMA weighter bootstrapped: %d factors, %d IC observations",
            len(weights),
            len(ic_df),
        )

    return weights
