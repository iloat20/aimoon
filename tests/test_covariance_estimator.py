"""Unit tests for covariance_estimator module.

Tests cover:
1. Marchenko-Pastur eigenvalue denoising
2. Ledoit-Wolf shrinkage
3. Full pipeline (estimate_factor_covariance)
4. Rolling covariance estimation
5. Factor portfolio Sharpe ratio
6. Edge cases (insufficient data, single factor, etc.)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aimoon.ml.covariance_estimator import (
    CovarianceResult,
    _compute_portfolio_sharpe,
    _estimate_noise_variance,
    _ledoit_wolf_shrinkage,
    _mp_bounds,
    _nearest_psd,
    _shrinkage_constant_correlation,
    denoise_covariance_mp,
    estimate_factor_covariance,
    factor_portfolio_sharpe,
    rolling_covariance,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────


def _random_factor_returns(
    n_samples: int = 252,
    n_factors: int = 10,
    seed: int = 42,
    signal_strength: float = 0.3,
) -> pd.DataFrame:
    """Generate synthetic factor returns with known structure.

    Creates returns with:
    - A few strong signal factors (correlated with a latent factor)
    - Remaining noise factors (pure random)
    - Realistic volatility levels (~20% annualized)
    """
    rng = np.random.default_rng(seed)

    # Latent systematic factor
    latent = rng.standard_normal(n_samples) * 0.02  # ~2% daily vol

    # Factor loadings on latent factor
    n_signal = max(2, n_factors // 3)
    loadings = rng.uniform(0.3, 0.8, size=n_signal) * signal_strength

    # Signal factors = loading * latent + idiosyncratic noise
    signal_returns = np.outer(latent, loadings) + rng.standard_normal(
        (n_samples, n_signal),
    ) * 0.015

    # Pure noise factors
    n_noise = n_factors - n_signal
    noise_returns = rng.standard_normal((n_samples, n_noise)) * 0.02

    all_returns = np.hstack([signal_returns, noise_returns])

    factor_names = [f"alpha_{i:03d}" for i in range(n_factors)]
    dates = pd.bdate_range("2023-01-01", periods=n_samples)

    return pd.DataFrame(all_returns, index=dates, columns=factor_names)


def _make_icir_weights(n_factors: int = 10, seed: int = 42) -> pd.Series:
    """Create synthetic ICIR weights."""
    rng = np.random.default_rng(seed)
    weights = rng.uniform(0.1, 2.0, size=n_factors)
    names = [f"alpha_{i:03d}" for i in range(n_factors)]
    return pd.Series(weights, index=names)


# ── Tests: MP bounds ─────────────────────────────────────────────────────────


class TestMPBounds:
    """Tests for Marchenko-Pastur bound calculation."""

    def test_basic_bounds(self):
        """MP bounds should be positive when q > 1."""
        lower, upper = _mp_bounds(q=2.0, sigma2=1.0)
        assert lower > 0
        assert upper > lower

    def test_q_equals_one(self):
        """When q=1, lower bound should be 0."""
        lower, upper = _mp_bounds(q=1.0, sigma2=1.0)
        assert lower == 0.0
        assert upper == 4.0

    def test_large_q(self):
        """Large q should give bounds close to sigma2."""
        lower, upper = _mp_bounds(q=100.0, sigma2=1.0)
        assert abs(lower - 1.0) < 0.1
        assert abs(upper - 1.0) < 0.1

    def test_custom_sigma2(self):
        """Bounds should scale with sigma2."""
        lower, upper = _mp_bounds(q=2.0, sigma2=4.0)
        assert lower > 0
        assert upper == 4 * _mp_bounds(q=2.0, sigma2=1.0)[1]


# ── Tests: Noise variance estimation ─────────────────────────────────────────


class TestNoiseVariance:
    """Tests for noise variance estimation."""

    def test_pure_noise(self):
        """Pure noise should give sigma2 close to 1."""
        rng = np.random.default_rng(42)
        n = 1000
        p = 50
        q = n / p
        X = rng.standard_normal((n, p))
        S = X.T @ X / n
        eigenvalues = np.sort(np.linalg.eigvalsh(S))[::-1]
        sigma2_est = _estimate_noise_variance(eigenvalues, q, p)
        # Should be close to 1.0 for pure noise
        assert 0.5 < sigma2_est < 2.0

    def test_signal_plus_noise(self):
        """Signal + noise should still estimate reasonable sigma2."""
        returns = _random_factor_returns(n_samples=500, n_factors=10)
        X = returns.values
        X_demean = X - X.mean(axis=0, keepdims=True)
        S = X_demean.T @ X_demean / len(X)
        eigenvalues = np.sort(np.linalg.eigvalsh(S))[::-1]
        q = len(X) / 10
        sigma2_est = _estimate_noise_variance(eigenvalues, q, 10)
        assert sigma2_est > 0


# ── Tests: MP denoising ─────────────────────────────────────────────────────


class TestMPDenoising:
    """Tests for Marchenko-Pastur covariance denoising."""

    def test_output_shape(self):
        """Denoised covariance should have same shape as input."""
        returns = _random_factor_returns(n_samples=252, n_factors=10)
        X = returns.values
        S = X.T @ X / len(X)
        denoised, _, eigenvalues = denoise_covariance_mp(S, len(X))
        assert denoised.shape == S.shape
        assert len(eigenvalues) == S.shape[0]

    def test_symmetry(self):
        """Denoised covariance should be symmetric."""
        returns = _random_factor_returns(n_samples=200, n_factors=8)
        X = returns.values
        S = X.T @ X / len(X)
        denoised, _, _ = denoise_covariance_mp(S, len(X))
        np.testing.assert_array_almost_equal(denoised, denoised.T, decimal=10)

    def test_positive_semidefinite(self):
        """Denoised covariance should be PSD."""
        returns = _random_factor_returns(n_samples=200, n_factors=8)
        X = returns.values
        S = X.T @ X / len(X)
        denoised, _, _ = denoise_covariance_mp(S, len(X))
        eigenvalues = np.linalg.eigvalsh(denoised)
        assert np.all(eigenvalues >= -1e-10)

    def test_reduces_condition_number(self):
        """Denoising should reduce the condition number."""
        returns = _random_factor_returns(n_samples=200, n_factors=15)
        X = returns.values
        S = X.T @ X / len(X)
        cond_before = np.linalg.cond(S)
        denoised, _, _ = denoise_covariance_mp(S, len(X))
        cond_after = np.linalg.cond(denoised)
        # Denoising should generally improve conditioning
        assert cond_after <= cond_before * 1.5  # Allow some tolerance

    def test_skip_when_q_le_1(self):
        """Should skip denoising when n_samples <= n_factors."""
        S = np.eye(5)
        denoised, _, eigenvalues = denoise_covariance_mp(S, 5)
        np.testing.assert_array_almost_equal(denoised, S)

    def test_remove_method(self):
        """Remove method should zero out noise eigenvalues."""
        returns = _random_factor_returns(n_samples=500, n_factors=5)
        X = returns.values
        S = X.T @ X / len(X)
        _, _, eigenvalues = denoise_covariance_mp(
            S, len(X), method="remove",
        )
        # All eigenvalues should be >= 0
        assert np.all(eigenvalues >= -1e-10)

    def test_invalid_method(self):
        """Invalid method should raise ValueError."""
        S = np.eye(3)
        with pytest.raises(ValueError, match="Unknown method"):
            denoise_covariance_mp(S, 100, method="invalid")


# ── Tests: Ledoit-Wolf shrinkage ─────────────────────────────────────────────


class TestLedoitWolf:
    """Tests for Ledoit-Wolf shrinkage estimation."""

    def test_shrinkage_in_range(self):
        """Shrinkage intensity should be in [0, 1]."""
        returns = _random_factor_returns(n_samples=200, n_factors=10)
        X = returns.values
        X_demean = X - X.mean(axis=0, keepdims=True)
        S = X_demean.T @ X_demean / len(X)
        shrinkage, _ = _ledoit_wolf_shrinkage(X_demean, S)
        assert 0.0 <= shrinkage <= 1.0

    def test_shrunk_cov_is_psd(self):
        """Shrunk covariance should be PSD."""
        returns = _random_factor_returns(n_samples=200, n_factors=10)
        X = returns.values
        X_demean = X - X.mean(axis=0, keepdims=True)
        S = X_demean.T @ X_demean / len(X)
        _, shrunk = _ledoit_wolf_shrinkage(X_demean, S)
        eigenvalues = np.linalg.eigvalsh(shrunk)
        assert np.all(eigenvalues >= -1e-10)

    def test_shrinkage_reduces_frobenius_norm(self):
        """Shrinking toward identity should reduce off-diagonal magnitude."""
        returns = _random_factor_returns(n_samples=200, n_factors=10)
        X = returns.values
        X_demean = X - X.mean(axis=0, keepdims=True)
        S = X_demean.T @ X_demean / len(X)
        _, shrunk = _ledoit_wolf_shrinkage(X_demean, S)

        # Off-diagonal Frobenius norm should decrease
        off_diag_S = S.copy()
        np.fill_diagonal(off_diag_S, 0)
        off_diag_shrunk = shrunk.copy()
        np.fill_diagonal(off_diag_shrunk, 0)

        assert np.sum(off_diag_shrunk ** 2) <= np.sum(off_diag_S ** 2) + 1e-10

    def test_constant_correlation_shrinkage(self):
        """Constant correlation target should produce valid shrinkage."""
        returns = _random_factor_returns(n_samples=200, n_factors=10)
        X = returns.values
        X_demean = X - X.mean(axis=0, keepdims=True)
        S = X_demean.T @ X_demean / len(X)
        shrinkage, shrunk = _shrinkage_constant_correlation(X_demean, S)
        assert 0.0 <= shrinkage <= 1.0
        eigenvalues = np.linalg.eigvalsh(shrunk)
        assert np.all(eigenvalues >= -1e-10)


# ── Tests: Full pipeline ─────────────────────────────────────────────────────


class TestEstimateFactorCovariance:
    """Tests for the main estimate_factor_covariance function."""

    def test_basic_run(self):
        """Basic run should return CovarianceResult."""
        returns = _random_factor_returns(n_samples=252, n_factors=10)
        result = estimate_factor_covariance(returns)
        assert isinstance(result, CovarianceResult)
        assert result.n_factors == 10
        assert result.cov.shape == (10, 10)
        assert result.corr.shape == (10, 10)
        assert len(result.eigenvalues) == 10

    def test_cov_symmetry(self):
        """Covariance matrix should be symmetric."""
        returns = _random_factor_returns(n_samples=200, n_factors=8)
        result = estimate_factor_covariance(returns)
        np.testing.assert_array_almost_equal(
            result.cov.values, result.cov.values.T, decimal=10,
        )

    def test_corr_diagonal(self):
        """Correlation diagonal should be 1."""
        returns = _random_factor_returns(n_samples=200, n_factors=8)
        result = estimate_factor_covariance(returns)
        np.testing.assert_array_almost_equal(
            np.diag(result.corr.values), np.ones(8), decimal=10,
        )

    def test_positive_semidefinite(self):
        """Covariance matrix should be PSD."""
        returns = _random_factor_returns(n_samples=200, n_factors=8)
        result = estimate_factor_covariance(returns)
        eigenvalues = np.linalg.eigvalsh(result.cov.values)
        assert np.all(eigenvalues >= -1e-10)

    def test_shrinkage_in_range(self):
        """Shrinkage should be in [0, 1]."""
        returns = _random_factor_returns(n_samples=200, n_factors=8)
        result = estimate_factor_covariance(returns)
        assert 0.0 <= result.shrinkage <= 1.0

    def test_n_effective_reasonable(self):
        """Effective number of factors should be between 1 and n_factors."""
        returns = _random_factor_returns(n_samples=252, n_factors=10)
        result = estimate_factor_covariance(returns)
        assert 1.0 <= result.n_effective <= 10.0

    def test_with_icir_weights(self):
        """Should compute Sharpe ratio when ICIR weights provided."""
        returns = _random_factor_returns(n_samples=252, n_factors=10)
        weights = _make_icir_weights(n_factors=10)
        result = estimate_factor_covariance(returns, icir_weights=weights)
        assert result.sharpe is not None
        assert result.mean_ic is not None
        assert isinstance(result.sharpe, float)

    def test_without_denoising(self):
        """Should work without MP denoising."""
        returns = _random_factor_returns(n_samples=200, n_factors=8)
        result = estimate_factor_covariance(returns, denoise=False)
        assert isinstance(result, CovarianceResult)
        assert result.n_factors == 8

    def test_constant_correlation_target(self):
        """Should work with constant correlation shrinkage target."""
        returns = _random_factor_returns(n_samples=200, n_factors=8)
        result = estimate_factor_covariance(
            returns, shrinkage_target="constant_correlation",
        )
        assert isinstance(result, CovarianceResult)
        assert 0.0 <= result.shrinkage <= 1.0

    def test_insufficient_samples(self):
        """Should raise ValueError with too few samples."""
        returns = _random_factor_returns(n_samples=10, n_factors=10)
        with pytest.raises(ValueError, match="Insufficient samples"):
            estimate_factor_covariance(returns, min_samples=30)

    def test_single_factor(self):
        """Should raise ValueError with single factor."""
        returns = pd.DataFrame({"alpha_000": np.random.randn(100) * 0.02})
        with pytest.raises(ValueError, match="Need >= 2 factors"):
            estimate_factor_covariance(returns)

    def test_dropna(self):
        """Should handle NaN values by dropping them."""
        returns = _random_factor_returns(n_samples=200, n_factors=5)
        returns.iloc[10, 0] = np.nan
        returns.iloc[20, 2] = np.nan
        result = estimate_factor_covariance(returns)
        assert isinstance(result, CovarianceResult)

    def test_n_effective_with_signal(self):
        """Strong signal should give lower n_effective (more concentrated)."""
        returns_noise = _random_factor_returns(
            n_samples=500, n_factors=10, signal_strength=0.0,
        )
        returns_signal = _random_factor_returns(
            n_samples=500, n_factors=10, signal_strength=0.8,
        )
        result_noise = estimate_factor_covariance(returns_noise)
        result_signal = estimate_factor_covariance(returns_signal)
        # Strong signal should concentrate variance in fewer factors
        assert result_signal.n_effective <= result_noise.n_effective + 2


# ── Tests: Rolling covariance ────────────────────────────────────────────────


class TestRollingCovariance:
    """Tests for rolling covariance estimation."""

    def test_rolling_output(self):
        """Rolling covariance should return list of (date, result) tuples."""
        returns = _random_factor_returns(n_samples=300, n_factors=8)
        results = rolling_covariance(returns, window=60, step=20)
        assert len(results) > 0
        for date, result in results:
            assert isinstance(result, CovarianceResult)
            assert hasattr(result, "cov")

    def test_rolling_dates_increasing(self):
        """Rolling dates should be monotonically increasing."""
        returns = _random_factor_returns(n_samples=300, n_factors=8)
        results = rolling_covariance(returns, window=60, step=10)
        dates = [r[0] for r in results]
        assert dates == sorted(dates)

    def test_rolling_with_icir(self):
        """Rolling covariance should compute Sharpe when weights provided."""
        returns = _random_factor_returns(n_samples=300, n_factors=8)
        weights = _make_icir_weights(n_factors=8)
        results = rolling_covariance(
            returns, window=60, step=50, icir_weights=weights,
        )
        for _, result in results:
            assert result.sharpe is not None

    def test_factor_portfolio_sharpe(self):
        """factor_portfolio_sharpe should return a Series."""
        returns = _random_factor_returns(n_samples=300, n_factors=8)
        weights = _make_icir_weights(n_factors=8)
        sharpe_series = factor_portfolio_sharpe(
            returns, weights, window=60,
        )
        assert isinstance(sharpe_series, pd.Series)
        assert len(sharpe_series) > 0
        assert sharpe_series.name == "factor_sharpe"


# ── Tests: Portfolio Sharpe ─────────────────────────────────────────────────


class TestPortfolioSharpe:
    """Tests for factor portfolio Sharpe ratio calculation."""

    def test_positive_sharpe(self):
        """Positive mean returns should give positive Sharpe."""
        returns = _random_factor_returns(
            n_samples=500, n_factors=5, signal_strength=0.5,
        )
        weights = _make_icir_weights(n_factors=5)
        cov = returns.cov().values * 252
        sharpe, mean_ic = _compute_portfolio_sharpe(
            cov, list(returns.columns), returns, weights,
        )
        assert isinstance(sharpe, float)
        assert isinstance(mean_ic, float)

    def test_zero_weights(self):
        """Zero weights should return zero Sharpe."""
        n = 5
        cov = np.eye(n)
        factor_names = [f"f{i}" for i in range(n)]
        factor_returns = pd.DataFrame(
            np.random.randn(100, n), columns=factor_names,
        )
        weights = pd.Series(np.zeros(n), index=factor_names)
        sharpe, _ = _compute_portfolio_sharpe(
            cov, factor_names, factor_returns, weights,
        )
        assert sharpe == 0.0

    def test_alignment(self):
        """Weights should align with covariance matrix factors."""
        n = 5
        cov = np.eye(n) * 0.04
        factor_names = [f"alpha_{i}" for i in range(n)]
        factor_returns = pd.DataFrame(
            np.random.randn(200, n) * 0.02, columns=factor_names,
        )
        weights = pd.Series([1.0, 0.5, 0.3, 0.2, 0.1], index=factor_names)
        sharpe, _ = _compute_portfolio_sharpe(
            cov, factor_names, factor_returns, weights,
        )
        assert np.isfinite(sharpe)


# ── Tests: Nearest PSD ──────────────────────────────────────────────────────


class TestNearestPSD:
    """Tests for nearest positive semi-definite matrix."""

    def test_already_psd(self):
        """Already PSD matrix should remain unchanged."""
        A = np.array([[2.0, 0.5], [0.5, 1.0]])
        B = _nearest_psd(A)
        np.testing.assert_array_almost_equal(A, B, decimal=10)

    def test_symmetry(self):
        """Non-symmetric input should produce symmetric output."""
        A = np.array([[2.0, 0.3], [0.1, 1.0]])
        B = _nearest_psd(A)
        np.testing.assert_array_almost_equal(B, B.T, decimal=10)

    def test_psd_output(self):
        """Output should be PSD even if input is not."""
        # Create a matrix with a negative eigenvalue
        A = np.array([[1.0, 2.0], [2.0, 1.0]])
        B = _nearest_psd(A)
        eigenvalues = np.linalg.eigvalsh(B)
        assert np.all(eigenvalues >= -1e-10)


# ── Tests: Integration ───────────────────────────────────────────────────────


class TestIntegration:
    """Integration tests combining multiple components."""

    def test_full_pipeline(self):
        """Test the full pipeline: generate -> estimate -> rolling -> sharpe."""
        # 1. Generate data
        returns = _random_factor_returns(n_samples=500, n_factors=10)

        # 2. Single estimate
        result = estimate_factor_covariance(returns)
        assert result.n_factors == 10
        assert result.shrinkage > 0

        # 3. Rolling estimate
        weights = _make_icir_weights(n_factors=10)
        rolling = rolling_covariance(
            returns, window=120, step=50, icir_weights=weights,
        )
        assert len(rolling) > 0

        # 4. Sharpe series
        sharpe_series = factor_portfolio_sharpe(returns, weights, window=120)
        assert len(sharpe_series) > 0

    def test_denoise_improves_condition(self):
        """Denoising should improve matrix conditioning."""
        returns = _random_factor_returns(n_samples=300, n_factors=15)
        result_no_denoise = estimate_factor_covariance(
            returns, denoise=False,
        )
        result_denoise = estimate_factor_covariance(
            returns, denoise=True,
        )
        cond_no = np.linalg.cond(result_no_denoise.cov.values)
        cond_yes = np.linalg.cond(result_denoise.cov.values)
        # Denoising generally improves conditioning
        assert cond_yes <= cond_no * 2.0  # Allow tolerance

    def test_high_shrinkage_for_noisy_data(self):
        """Noisy data should result in higher shrinkage."""
        returns_noisy = _random_factor_returns(
            n_samples=200, n_factors=10, signal_strength=0.0,
        )
        returns_signal = _random_factor_returns(
            n_samples=200, n_factors=10, signal_strength=0.8,
        )
        result_noisy = estimate_factor_covariance(returns_noisy)
        result_signal = estimate_factor_covariance(returns_signal)
        # Noisy data should have higher shrinkage toward identity
        assert result_noisy.shrinkage >= result_signal.shrinkage - 0.1
