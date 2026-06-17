"""Robust factor covariance estimation with Ledoit-Wolf shrinkage and
Marchenko-Pastur denoising.

Provides daily factor covariance matrices suitable for:
- Black-Litterman posterior returns
- Risk parity / mean-variance optimization
- Factor portfolio Sharpe ratio calculation (combined with ICIR weights)

References
----------
- Ledoit, O. & Wolf, M. (2004). "A well-conditioned estimator for
  large-dimensional covariance matrices." Journal of Multivariate Analysis.
- Marchenko, V. A. & Pastur, L. A. (1967). "Distribution of eigenvalues
  for some sets of random matrices." Mathematics of the USSR-Sbornik.
- Grinold, R. C. & Kahn, R. N. (2000). "Active Portfolio Management."
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from numpy.linalg import eigh

logger = logging.getLogger(__name__)


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class CovarianceResult:
    """Container for covariance estimation results.

    Attributes
    ----------
    cov : pd.DataFrame
        Estimated covariance matrix (factors x factors).
    corr : pd.DataFrame
        Estimated correlation matrix.
    eigenvalues : np.ndarray
        Eigenvalues of the denoised covariance matrix.
    n_factors : int
        Number of factors.
    n_effective : float
        Effective number of factors (inverse participation ratio).
    shrinkage : float
        Optimal shrinkage intensity used.
    mean_ic : float | None
        Mean IC across factors (if IC series provided).
    sharpe : float | None
        Annualized factor portfolio Sharpe ratio (if ICIR weights provided).
    """

    cov: pd.DataFrame
    corr: pd.DataFrame
    eigenvalues: np.ndarray
    n_factors: int
    n_effective: float
    shrinkage: float
    mean_ic: float | None = None
    sharpe: float | None = None


# ── Marchenko-Pastur denoising ──────────────────────────────────────────────


def _mp_bounds(q: float, sigma2: float = 1.0) -> tuple[float, float]:
    """Compute Marchenko-Pastur lower and upper bounds.

    Parameters
    ----------
    q : float
        Ratio n_samples / n_factors (must be > 1).
    sigma2 : float
        Variance of random noise (default 1.0).

    Returns
    -------
    tuple[float, float]
        (lower_bound, upper_bound) for the MP spectrum.
    """
    lambda_minus = sigma2 * (1 - 1.0 / q) ** 2
    lambda_plus = sigma2 * (1 + 1.0 / q) ** 2
    return lambda_minus, lambda_plus


def _estimate_noise_variance(
    eigenvalues: np.ndarray,
    q: float,
    n_factors: int,
) -> float:
    """Estimate the noise variance from eigenvalues below the MP upper bound.

    Uses the bulk of the spectrum (eigenvalues <= MP upper bound) to
    estimate sigma^2, since these are dominated by noise.

    Parameters
    ----------
    eigenvalues : np.ndarray
        Sorted eigenvalues of the sample covariance.
    q : float
        n_samples / n_factors.
    n_factors : int
        Number of factors.

    Returns
    -------
    float
        Estimated noise variance.
    """
    lambda_minus, lambda_plus = _mp_bounds(q)
    noise_eigenvalues = eigenvalues[eigenvalues <= lambda_plus]
    if len(noise_eigenvalues) == 0:
        return 1.0
    return float(np.mean(noise_eigenvalues))


def denoise_covariance_mp(
    cov: np.ndarray,
    n_samples: int,
    *,
    method: str = "clip",
) -> tuple[np.ndarray, float, np.ndarray]:
    """Denoise a covariance matrix via Marchenko-Pastur eigenvalue clipping.

    Replaces eigenvalues in the MP bulk (attributable to noise) with a
    constant value, preserving the signal eigenvalues above the MP upper bound.

    Parameters
    ----------
    cov : np.ndarray
        Sample covariance matrix (n_factors x n_factors).
    n_samples : int
        Number of observations used to estimate the covariance.
    method : str
        "clip" — replace noise eigenvalues with their mean.
        "remove" — set noise eigenvalues to zero (hard threshold).

    Returns
    -------
    tuple[np.ndarray, float, np.ndarray]
        (denoised_cov, noise_variance, eigenvalues).
    """
    n_factors = cov.shape[0]
    if n_factors < 2:
        return cov, 1.0, np.linalg.eigvalsh(cov)

    q = n_samples / n_factors
    if q <= 1:
        logger.warning(
            "n_samples (%d) <= n_factors (%d): MP denoising skipped",
            n_samples,
            n_factors,
        )
        return cov, 1.0, np.linalg.eigvalsh(cov)

    # Eigendecomposition
    eigenvalues, eigenvectors = eigh(cov)

    # Estimate noise variance from the bulk
    sigma2 = _estimate_noise_variance(eigenvalues, q, n_factors)

    # MP upper bound
    _, lambda_plus = _mp_bounds(q, sigma2)

    # Denoise
    denoised_eigenvalues = eigenvalues.copy()
    if method == "clip":
        noise_mask = eigenvalues <= lambda_plus
        if noise_mask.any():
            denoised_eigenvalues[noise_mask] = sigma2
    elif method == "remove":
        denoised_eigenvalues[eigenvalues <= lambda_plus] = 0.0
    else:
        raise ValueError(f"Unknown method: {method}")

    # Reconstruct
    denoised_cov = eigenvectors @ np.diag(denoised_eigenvalues) @ eigenvectors.T

    # Ensure symmetry
    denoised_cov = (denoised_cov + denoised_cov.T) / 2.0

    return denoised_cov, sigma2, denoised_eigenvalues


# ── Ledoit-Wolf shrinkage ────────────────────────────────────────────────────


def _ledoit_wolf_shrinkage(
    X: np.ndarray,
    S: np.ndarray,
) -> tuple[float, np.ndarray]:
    """Compute the Ledoit-Wolf oracle shrinkage intensity and shrunk covariance.

    Implements the Ledoit-Wolf (2004) oracle approximation for optimal
    shrinkage intensity. The target is the scaled identity matrix.

    Parameters
    ----------
    X : np.ndarray
        Return matrix (n_samples x n_factors), already demeaned.
    S : np.ndarray
        Sample covariance matrix (n_factors x n_factors).

    Returns
    -------
    tuple[float, np.ndarray]
        (optimal_shrinkage, shrunk_covariance).
    """
    n_samples, n_factors = X.shape

    # Sample covariance (already computed, but we need the demeaned version)
    # S = X.T @ X / n_samples

    # Target: scaled identity (mu * I)
    mu = np.trace(S) / n_factors
    target = mu * np.eye(n_factors)

    # Compute the Frobenius norm terms for the oracle
    # sum of squared off-diagonal elements of S
    S2 = S**2
    sum_sq = np.sum(S2) - np.sum(np.diag(S2))

    # Estimate the sum of squared covariances of sample covariances
    # Using the oracle approximation
    X2 = X**2  # element-wise square

    # Compute delta^2 = sum of variances of S_ij
    # delta^2 = (1/n) * sum_t (x_t x_t' - S)^2  (Frobenius norm)
    # Using the simplified oracle:
    term1 = (X2.T @ X2) / n_samples - S2
    delta2 = np.sum(term1**2)

    # beta^2 = sum of squared off-diagonal elements of S (the "bias" term)
    beta2 = sum_sq

    # Optimal shrinkage
    kappa = (delta2 - beta2) / (n_samples * (beta2 + (mu**2) * n_factors))
    shrinkage = max(0.0, min(1.0, kappa / (kappa + 1.0)))

    # Shrunk covariance
    shrunk = shrinkage * target + (1.0 - shrinkage) * S

    return float(shrinkage), shrunk


# ── Main estimator ───────────────────────────────────────────────────────────


def estimate_factor_covariance(
    factor_returns: pd.DataFrame,
    *,
    min_samples: int = 30,
    shrinkage_target: str = "scaled_identity",
    denoise: bool = True,
    denoise_method: str = "clip",
    icir_weights: pd.Series | None = None,
) -> CovarianceResult:
    """Estimate robust factor covariance with Ledoit-Wolf + MP denoising.

    Pipeline:
    1. Demean factor returns.
    2. Compute sample covariance.
    3. Apply Ledoit-Wolf shrinkage toward scaled identity.
    4. Apply MP eigenvalue denoising (optional).
    5. Compute effective number of factors.
    6. Optionally compute factor portfolio Sharpe ratio.

    Parameters
    ----------
    factor_returns : pd.DataFrame
        T x K matrix of factor returns (T observations, K factors).
        Each column is a factor's daily return series.
    min_samples : int
        Minimum number of observations required.
    shrinkage_target : str
        Shrinkage target: "scaled_identity" (Ledoit-Wolf) or "constant_correlation".
    denoise : bool
        Whether to apply MP eigenvalue denoising.
    denoise_method : str
        MP denoising method: "clip" or "remove".
    icir_weights : pd.Series | None
        ICIR-based weights for each factor. If provided, computes the
        factor portfolio's annualized Sharpe ratio.

    Returns
    -------
    CovarianceResult
        Contains cov, corr, eigenvalues, n_effective, shrinkage, sharpe.
    """
    factor_returns = factor_returns.dropna()
    n_samples, n_factors = factor_returns.shape

    if n_samples < min_samples:
        raise ValueError(
            f"Insufficient samples: {n_samples} < {min_samples} "
            f"(need >= {min_samples} observations)"
        )

    if n_factors < 2:
        raise ValueError(f"Need >= 2 factors, got {n_factors}")

    factor_names = list(factor_returns.columns)
    X = factor_returns.values

    # Demean
    X_demean = X - X.mean(axis=0, keepdims=True)

    # Sample covariance
    S = X_demean.T @ X_demean / n_samples

    # Step 1: Ledoit-Wolf shrinkage
    if shrinkage_target == "scaled_identity":
        shrinkage, cov_shrunk = _ledoit_wolf_shrinkage(X_demean, S)
    elif shrinkage_target == "constant_correlation":
        shrinkage, cov_shrunk = _shrinkage_constant_correlation(X_demean, S)
    else:
        shrinkage, cov_shrunk = 0.0, S

    # Step 2: MP denoising
    if denoise:
        cov_denoised, noise_var, eigenvalues = denoise_covariance_mp(
            cov_shrunk,
            n_samples,
            method=denoise_method,
        )
    else:
        eigenvalues = np.linalg.eigvalsh(cov_shrunk)
        cov_denoised = cov_shrunk

    # Ensure positive semi-definite
    cov_denoised = _nearest_psd(cov_denoised)

    # Effective number of factors (inverse participation ratio)
    eigenvalues_sorted = np.sort(eigenvalues)[::-1]
    ipr = np.sum(eigenvalues_sorted**4) / (np.sum(eigenvalues_sorted**2) ** 2)
    n_effective = 1.0 / ipr if ipr > 0 else float(n_factors)

    # Correlation matrix
    std = np.sqrt(np.diag(cov_denoised))
    corr_matrix = cov_denoised / np.outer(std, std)

    cov_df = pd.DataFrame(cov_denoised, index=factor_names, columns=factor_names)
    corr_df = pd.DataFrame(corr_matrix, index=factor_names, columns=factor_names)

    # Factor portfolio Sharpe ratio (if ICIR weights provided)
    sharpe = None
    mean_ic = None
    if icir_weights is not None:
        sharpe, mean_ic = _compute_portfolio_sharpe(
            cov_denoised,
            factor_names,
            factor_returns,
            icir_weights,
        )

    return CovarianceResult(
        cov=cov_df,
        corr=corr_df,
        eigenvalues=eigenvalues_sorted,
        n_factors=n_factors,
        n_effective=n_effective,
        shrinkage=shrinkage,
        mean_ic=mean_ic,
        sharpe=sharpe,
    )


def _shrinkage_constant_correlation(
    X: np.ndarray,
    S: np.ndarray,
) -> tuple[float, np.ndarray]:
    """Ledoit-Wolf shrinkage toward the constant correlation target.

    The target matrix has the same diagonal as S and off-diagonal elements
    set to the average correlation times the geometric mean of the
    corresponding variances.
    """
    n_samples, n_factors = X.shape
    std = np.sqrt(np.diag(S))
    corr = S / np.outer(std, std)
    avg_corr = (np.sum(corr) - n_factors) / (n_factors * (n_factors - 1))

    # Target: constant correlation matrix
    target = np.outer(std, std) * avg_corr
    np.fill_diagonal(target, np.diag(S))

    # Use the same oracle shrinkage formula as scaled identity
    # (simplified; full LW for constant correlation is more complex)
    S2 = S**2
    sum_sq = np.sum(S2) - np.sum(np.diag(S2))
    X2 = X**2
    term1 = (X2.T @ X2) / n_samples - S2
    delta2 = np.sum(term1**2)
    beta2 = sum_sq

    mu = np.trace(S) / n_factors
    kappa = (delta2 - beta2) / (n_samples * (beta2 + mu**2 * n_factors))
    shrinkage = max(0.0, min(1.0, kappa / (kappa + 1.0)))

    shrunk = shrinkage * target + (1.0 - shrinkage) * S
    return float(shrinkage), shrunk


def _nearest_psd(A: np.ndarray) -> np.ndarray:
    """Find the nearest positive semi-definite matrix to A (Higham 2002)."""
    B = (A + A.T) / 2.0
    eigenvalues, eigenvectors = eigh(B)
    eigenvalues = np.maximum(eigenvalues, 1e-10)
    return eigenvectors @ np.diag(eigenvalues) @ eigenvectors.T


def _compute_portfolio_sharpe(
    cov: np.ndarray,
    factor_names: list[str],
    factor_returns: pd.DataFrame,
    icir_weights: pd.Series,
) -> tuple[float, float]:
    """Compute the annualized Sharpe ratio of the ICIR-weighted factor portfolio.

    Parameters
    ----------
    cov : np.ndarray
        Factor covariance matrix (K x K).
    factor_names : list[str]
        Factor name labels for alignment.
    factor_returns : pd.DataFrame
        Factor return series (T x K).
    icir_weights : pd.Series
        ICIR weights for each factor (index = factor names).

    Returns
    -------
    tuple[float, float]
        (annualized_sharpe, mean_portfolio_ic).
    """
    # Align weights with covariance matrix
    common_factors = [f for f in factor_names if f in factor_returns.columns]
    if not common_factors:
        return 0.0, 0.0

    # Extract weights as numpy array
    w = np.array([icir_weights.get(f, 0.0) for f in common_factors], dtype=np.float64)

    # Normalize to sum to 1
    w_sum = np.sum(np.abs(w))
    if w_sum < 1e-10:
        return 0.0, 0.0
    w = w / w_sum

    # Portfolio mean and variance
    mean_returns = factor_returns[common_factors].mean().values
    port_mean = float(w @ mean_returns) * 252  # annualized
    port_var = float(w @ cov @ w) * 252  # annualized

    if port_var < 1e-10:
        return 0.0, 0.0

    sharpe = port_mean / np.sqrt(port_var)

    # Mean IC: average IC across factors weighted by |w|
    abs_w = np.abs(w) / np.sum(np.abs(w))
    mean_ic = float(np.sum(abs_w * np.abs(mean_returns)))

    return sharpe, mean_ic


# ── Convenience functions ─────────────────────────────────────────────────────


def rolling_covariance(
    factor_returns: pd.DataFrame,
    window: int = 60,
    step: int = 1,
    *,
    denoise: bool = True,
    icir_weights: pd.Series | None = None,
) -> list[tuple[Any, CovarianceResult]]:
    """Compute rolling covariance estimates over time.

    Parameters
    ----------
    factor_returns : pd.DataFrame
        Full factor return history (T x K).
    window : int
        Rolling window size (in observations).
    step : int
        Step size for rolling.
    denoise : bool
        Apply MP denoising.
    icir_weights : pd.Series | None
        Optional ICIR weights for Sharpe calculation.

    Returns
    -------
    list[tuple[Any, CovarianceResult]]
        List of (date, CovarianceResult) tuples.
    """
    results: list[tuple[Any, CovarianceResult]] = []

    for start in range(0, len(factor_returns) - window + 1, step):
        end = start + window
        window_data = factor_returns.iloc[start:end]
        date = factor_returns.index[end - 1]

        try:
            result = estimate_factor_covariance(
                window_data,
                denoise=denoise,
                icir_weights=icir_weights,
            )
            results.append((date, result))
        except (ValueError, np.linalg.LinAlgError) as e:
            logger.debug("Covariance estimation failed at %s: %s", date, e)

    return results


def factor_portfolio_sharpe(
    factor_returns: pd.DataFrame,
    icir_weights: pd.Series,
    *,
    window: int = 60,
) -> pd.Series:
    """Compute rolling factor portfolio Sharpe ratio.

    Parameters
    ----------
    factor_returns : pd.DataFrame
        Factor return history.
    icir_weights : pd.Series
        ICIR weights per factor.
    window : int
        Estimation window.

    Returns
    -------
    pd.Series
        Rolling annualized Sharpe ratio indexed by date.
    """
    rolling = rolling_covariance(
        factor_returns,
        window=window,
        icir_weights=icir_weights,
    )

    if not rolling:
        return pd.Series(dtype=float)

    dates = [r[0] for r in rolling]
    sharpe_values = [r[1].sharpe for r in rolling]
    return pd.Series(sharpe_values, index=dates, name="factor_sharpe")
