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
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import ConstantInputWarning, spearmanr

from aimoon.factors.registry import Registry, get_default_registry
from aimoon.ml.label_engine import generate_labels

logger = logging.getLogger(__name__)

_ICIR_CACHE_TTL_HOURS = 72  # 3 days


def compute_factor_ic_series(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    registry: Registry | None = None,
    n_dates: int = 60,
    forward_days: int = 22,
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

            # 跳过无方差或近零方差输入（spearmanr 要求非常量输入）
            f_std, l_std = np.std(factor_vals), np.std(label_vals)
            if f_std < 1e-12 or l_std < 1e-12:
                continue

            try:
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=ConstantInputWarning)
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
    decay_halflife: int = 20,
    min_icir: float = 0.0,
    skew_threshold: float = -1.0,
) -> dict[str, float]:
    """Compute ICIR weights from IC time series.

    ICIR = mean(IC) / std(IC), with exponential decay weighting (half-life
    = decay_halflife periods).  When the cross-factor IC distribution
    skewness < skew_threshold, falls back to equal weights.

    Parameters
    ----------
    ic_df : pd.DataFrame
        IC time series from compute_factor_ic_series.
    decay_halflife : int
        Half-life in periods for exponential decay weighting.
    min_icir : float
        Minimum ICIR to include a factor (filters noise).
    skew_threshold : float
        Skewness threshold for regime switch to equal weights.

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

    # Regime check: skewness of most recent cross-sectional IC
    recent_ic = ic_df.iloc[-1].dropna()
    if len(recent_ic) >= 10:
        arr = recent_ic.values
        mean, std = arr.mean(), arr.std()
        if std > 1e-10:
            skew = float(((arr - mean) ** 3).mean() / (std**3))
            if skew < skew_threshold:
                logger.warning(
                    "compute_icir_weights: skewness=%.2f < %.1f → equal weights (%d factors)",
                    skew,
                    skew_threshold,
                    len(recent_ic),
                )
                n_factors = len(recent_ic)
                return {k: 1.0 / n_factors for k in recent_ic.index}

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

    Regime switch:
        When the cross-factor IC distribution skewness < -1, the factor
        universe is deemed unstable and all weights fall back to equal
        allocation (1/N per factor).

    Parameters
    ----------
    decay : float
        EWMA decay factor (0 < decay < 1). Default corresponds to
        half-life of 20 observations: exp(-ln(2)/20) ≈ 0.9659.
    min_weight : float
        Minimum weight floor (0 = allow zero-weight factors).
    skew_threshold : float
        Skewness threshold for regime switch. When the IC distribution
        skewness drops below this value, fall back to equal weights.
    """

    _DEFAULT_DECAY = np.exp(-np.log(2) / 20)  # half-life = 20

    def __init__(
        self,
        decay: float = _DEFAULT_DECAY,
        min_weight: float = 0.0,
        skew_threshold: float = -1.0,
    ):
        self._decay = decay
        self._min_weight = min_weight
        self._skew_threshold = skew_threshold
        self._ewma_mean: dict[str, float] = {}
        self._ewma_var: dict[str, float] = {}
        self._n_updates: int = 0
        self._ic_history: dict[str, list[float]] = {}
        self._equal_weight_active: bool = False

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

            # Track full IC history for skewness computation
            if factor_id not in self._ic_history:
                self._ic_history[factor_id] = []
            self._ic_history[factor_id].append(ic)
            # Keep at most 60 observations for skewness window
            if len(self._ic_history[factor_id]) > 60:
                self._ic_history[factor_id] = self._ic_history[factor_id][-60:]

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

        # Regime check: compute cross-factor IC skewness
        self._equal_weight_active = self._check_regime()

        weights = self.get_weights()

        logger.debug(
            "EWMA update #%d: %d factors, top=%s (%.4f), equal_weight=%s",
            self._n_updates,
            len(weights),
            max(weights, key=weights.get) if weights else "none",  # type: ignore[arg-type]
            max(weights.values()) if weights else 0.0,
            self._equal_weight_active,
        )
        return weights

    def _check_regime(self) -> bool:
        """Check if IC distribution skewness indicates unstable regime.

        Collects the most recent IC value from each factor's history,
        computes the skewness of that cross-sectional distribution, and
        returns True if skewness < skew_threshold (meaning the IC
        distribution has a heavy left tail — many factors going negative).

        Returns
        -------
        bool
            True if regime switch to equal-weight should activate.
        """
        recent_ics: list[float] = []
        for history in self._ic_history.values():
            if history:
                recent_ics.append(history[-1])

        if len(recent_ics) < 10:
            return False

        arr = np.array(recent_ics)
        mean = arr.mean()
        std = arr.std()
        if std < 1e-10:
            return False

        skew = float(((arr - mean) ** 3).mean() / (std**3))
        if skew < self._skew_threshold:
            logger.warning(
                "Regime switch: IC skewness=%.2f < %.1f → equal weights (%d factors)",
                skew,
                self._skew_threshold,
                len(recent_ics),
            )
            return True
        return False

    def get_weights(self) -> dict[str, float]:
        """Return current normalized weights.

        When regime switch is active (IC skewness < threshold), returns
        equal weights (1/N) across all factors with EWMA data.

        Returns
        -------
        dict[str, float]
            factor_id -> weight (sums to 1.0). Empty if no data.
        """
        if not self._ewma_mean:
            return {}

        # Regime switch: fall back to equal weights
        if self._equal_weight_active:
            n = len(self._ewma_mean)
            if n == 0:
                return {}
            w = 1.0 / n
            return {factor_id: w for factor_id in self._ewma_mean}

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

    def apply_ic_momentum(self):
        """Apply IC momentum boost: factors with improving IC get extra weight."""
        if not hasattr(self, "_ic_history"):
            return
        for factor_id, history in self._ic_history.items():
            if len(history) >= 20:
                recent_5 = np.mean(history[-5:])
                recent_20 = np.mean(history[-20:])
                momentum = recent_5 - recent_20
                if momentum > 0.01:
                    current_mean = self._ewma_mean.get(factor_id, 0.0)
                    self._ewma_mean[factor_id] = current_mean * 1.2

    def apply_cusum_penalty(self, ic_series):
        """CUSUM fast response: penalize factors with sustained negative IC."""
        penalties = {}
        if not hasattr(self, "_ic_history"):
            return penalties
        for factor_id, ic_val in ic_series.items():
            if factor_id not in self._ic_history:
                self._ic_history[factor_id] = []
            self._ic_history[factor_id].append(ic_val)
            history = self._ic_history[factor_id]
            if len(history) >= 3:
                last_3 = history[-3:]
                if all(h < 0 for h in last_3) and sum(last_3) < -0.05:
                    penalties[factor_id] = 0.3
                else:
                    penalties[factor_id] = 1.0
            else:
                penalties[factor_id] = 1.0
        return penalties

    def set_adaptive_decay(self, regime):
        """Adjust EWMA decay based on market regime."""
        regime_decays = {
            "bull": 0.85,
            "bear": 0.90,
            "sideways": 0.95,
            "high_volatility": 0.88,
            "crisis": 0.85,
        }
        new_decay = regime_decays.get(regime, 0.95)
        self._decay = new_decay

    def get_icir_values(self) -> dict[str, float]:
        """Return raw ICIR values (not normalized) for diagnostics."""
        result: dict[str, float] = {}
        for factor_id, mean in self._ewma_mean.items():
            var = self._ewma_var.get(factor_id, 0.0)
            std = np.sqrt(max(var, 1e-10))
            result[factor_id] = mean / std
        return result

    def save(self, path: Path | None = None, cache_dir: Path = Path(".aimoon_cache")) -> None:
        """Persist state to JSON.

        Parameters
        ----------
        path : Path | None
            Explicit save path. If None, uses cache_dir / "icir" / "ewma_weighter.json".
        cache_dir : Path
            Cache directory (ignored if path is provided).
        """
        path = path or cache_dir / "icir" / "ewma_weighter.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "timestamp": time.time(),
            "decay": self._decay,
            "min_weight": self._min_weight,
            "skew_threshold": self._skew_threshold,
            "n_updates": self._n_updates,
            "ewma_mean": self._ewma_mean,
            "ewma_var": self._ewma_var,
            "equal_weight_active": self._equal_weight_active,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(
        cls,
        path: Path | None = None,
        cache_dir: Path = Path(".aimoon_cache"),
    ) -> EWMAFactorWeighter | None:
        """Load state from JSON. Returns None if file missing or corrupt.

        Parameters
        ----------
        path : Path | None
            Explicit load path. If None, uses cache_dir / "icir" / "ewma_weighter.json".
        cache_dir : Path
            Cache directory (ignored if path is provided).
        """
        path = path or cache_dir / "icir" / "ewma_weighter.json"
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            weighter = cls(
                decay=data.get("decay", EWMAFactorWeighter._DEFAULT_DECAY),
                min_weight=data.get("min_weight", 0.0),
                skew_threshold=data.get("skew_threshold", -1.0),
            )
            weighter._ewma_mean = data.get("ewma_mean", {})
            weighter._ewma_var = data.get("ewma_var", {})
            weighter._n_updates = data.get("n_updates", 0)
            weighter._equal_weight_active = data.get("equal_weight_active", False)
            return weighter
        except Exception as e:
            logger.debug("Failed to load EWMA weighter: %s", e)
            return None


def load_or_compute_ewma(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    registry: Registry | None = None,
    decay: float = EWMAFactorWeighter._DEFAULT_DECAY,
    cache_ttl_hours: float = _ICIR_CACHE_TTL_HOURS,
    factor_cache: dict[str, pd.DataFrame] | None = None,
    cache_dir: Path = Path(".aimoon_cache"),
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
    cache_dir : Path
        Cache directory path.

    Returns
    -------
    dict[str, float]
        factor_id -> normalized weight (sums to 1.0).
    """
    cache_dir = Path(cache_dir)
    ewma_cache_file = cache_dir / "icir" / "ewma_weighter.json"

    # Try loading cached state
    weighter = EWMAFactorWeighter.load(ewma_cache_file)
    if weighter is not None and weighter.n_updates > 0:
        try:
            age_hours = (time.time() - ewma_cache_file.stat().st_mtime) / 3600
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
        weighter.save(ewma_cache_file)
        logger.info(
            "EWMA weighter bootstrapped: %d factors, %d IC observations",
            len(weights),
            len(ic_df),
        )

    return weights


# ── Factor covariance + Sharpe ────────────────────────────────────────────────


def compute_factor_covariance_and_sharpe(
    ic_df: pd.DataFrame,
    icir_weights: dict[str, float] | None = None,
    *,
    denoise: bool = True,
    min_samples: int = 30,
) -> dict[str, Any] | None:
    """Compute robust factor covariance and portfolio Sharpe from IC series.

    Uses the IC time series (which already exists from ICIR weight computation)
    as a proxy for factor quality. Higher IC -> higher signal -> higher expected
    factor return. The IC series is used to construct synthetic factor returns
    for covariance estimation.

    Pipeline:
    1. Use IC series as proxy for factor "returns" (cross-sectional IC ~ factor alpha).
    2. Apply Ledoit-Wolf shrinkage + MP denoising via covariance_estimator.
    3. If icir_weights provided, compute the ICIR-weighted factor portfolio Sharpe.

    Parameters
    ----------
    ic_df : pd.DataFrame
        IC time series (index=date, columns=factor_id, values=IC).
    icir_weights : dict | None
        ICIR weights for Sharpe calculation.
    denoise : bool
        Apply MP eigenvalue denoising.
    min_samples : int
        Minimum observations required.

    Returns
    -------
    dict | None
        Keys: cov (DataFrame), corr (DataFrame), eigenvalues (ndarray),
        n_effective (float), shrinkage (float), sharpe (float | None),
        mean_ic (float | None). None if insufficient data.
    """
    if ic_df.empty or len(ic_df) < min_samples:
        return None

    try:
        from aimoon.ml.covariance_estimator import estimate_factor_covariance
    except ImportError:
        logger.debug("covariance_estimator not available")
        return None

    # Use IC series as synthetic factor returns
    factor_returns = ic_df.dropna(axis=1, how="all")

    if factor_returns.shape[1] < 2 or factor_returns.shape[0] < min_samples:
        return None

    # Convert ICIR weights to Series if provided
    weights_series = None
    if icir_weights:
        weights_series = pd.Series(icir_weights)

    try:
        result = estimate_factor_covariance(
            factor_returns,
            min_samples=min_samples,
            denoise=denoise,
            icir_weights=weights_series,
        )
    except (ValueError, np.linalg.LinAlgError) as e:
        logger.debug("Covariance estimation failed: %s", e)
        return None

    return {
        "cov": result.cov,
        "corr": result.corr,
        "eigenvalues": result.eigenvalues,
        "n_effective": result.n_effective,
        "shrinkage": result.shrinkage,
        "sharpe": result.sharpe,
        "mean_ic": result.mean_ic,
    }
