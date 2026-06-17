"""Run covariance estimator tests manually."""
import sys
import traceback

import numpy as np
import pandas as pd

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

passed = 0
failed = 0


def run_test(name, fn):
    global passed, failed
    try:
        fn()
        print(f"  PASS: {name}")
        passed += 1
    except Exception as e:
        print(f"  FAIL: {name}: {e}")
        traceback.print_exc()
        failed += 1


def _random_returns(n=252, k=10, seed=42, signal=0.3):
    rng = np.random.default_rng(seed)
    latent = rng.standard_normal(n) * 0.02
    n_sig = max(2, k // 3)
    loadings = rng.uniform(0.3, 0.8, size=n_sig) * signal
    sig = np.outer(latent, loadings) + rng.standard_normal((n, n_sig)) * 0.015
    noise = rng.standard_normal((n, k - n_sig)) * 0.02
    all_r = np.hstack([sig, noise])
    cols = [f"alpha_{i:03d}" for i in range(k)]
    return pd.DataFrame(all_r, columns=cols, index=pd.bdate_range("2023-01-01", periods=n))


def _weights(k=10):
    rng = np.random.default_rng(42)
    return pd.Series(rng.uniform(0.1, 2.0, size=k), index=[f"alpha_{i:03d}" for i in range(k)])


def _get_S(n=200, k=8):
    r = _random_returns(n, k)
    X = r.values - r.values.mean(axis=0, keepdims=True)
    return X.T @ X / n, r


def _get_X_S(n=200, k=10):
    r = _random_returns(n, k)
    X = r.values - r.values.mean(axis=0, keepdims=True)
    return X, X.T @ X / n


# --- MP Bounds ---
print("TestMPBounds:")


def test_basic_bounds():
    lower, upper = _mp_bounds(q=2.0, sigma2=1.0)
    assert lower > 0
    assert upper > lower


def test_q_equals_one():
    lower, upper = _mp_bounds(q=1.0, sigma2=1.0)
    assert lower == 0.0
    assert upper == 4.0


def test_large_q():
    lower, upper = _mp_bounds(q=100.0, sigma2=1.0)
    assert abs(lower - 1.0) < 0.1
    assert abs(upper - 1.0) < 0.1


def test_custom_sigma2():
    lower, upper = _mp_bounds(q=2.0, sigma2=4.0)
    assert lower > 0
    assert upper == 4 * _mp_bounds(q=2.0, sigma2=1.0)[1]


run_test("basic_bounds", test_basic_bounds)
run_test("q=1_lower=0", test_q_equals_one)
run_test("large_q", test_large_q)
run_test("custom_sigma2", test_custom_sigma2)

# --- Noise variance ---
print("\nTestNoiseVariance:")


def test_pure_noise():
    rng = np.random.default_rng(42)
    n, p = 1000, 50
    X = rng.standard_normal((n, p))
    S = X.T @ X / n
    eigenvalues = np.sort(np.linalg.eigvalsh(S))[::-1]
    sigma2_est = _estimate_noise_variance(eigenvalues, n / p, p)
    assert 0.5 < sigma2_est < 2.0


def test_signal_plus_noise():
    returns = _random_returns(n_samples=500, n_factors=10)
    X = returns.values
    X_demean = X - X.mean(axis=0, keepdims=True)
    S = X_demean.T @ X_demean / len(X)
    eigenvalues = np.sort(np.linalg.eigvalsh(S))[::-1]
    sigma2_est = _estimate_noise_variance(eigenvalues, len(X) / 10, 10)
    assert sigma2_est > 0


run_test("pure_noise", test_pure_noise)
run_test("signal_plus_noise", test_signal_plus_noise)

# --- MP Denoising ---
print("\nTestMPDenoising:")


def test_mp_output_shape():
    S, _ = _get_S(252, 10)
    denoised, _, eigenvalues = denoise_covariance_mp(S, 252)
    assert denoised.shape == S.shape
    assert len(eigenvalues) == S.shape[0]


def test_mp_symmetry():
    S, _ = _get_S(200, 8)
    denoised, _, _ = denoise_covariance_mp(S, 200)
    np.testing.assert_array_almost_equal(denoised, denoised.T, decimal=10)


def test_mp_psd():
    S, _ = _get_S(200, 8)
    denoised, _, _ = denoise_covariance_mp(S, 200)
    eigenvalues = np.linalg.eigvalsh(denoised)
    assert np.all(eigenvalues >= -1e-10)


def test_mp_skip_q_le_1():
    S = np.eye(5)
    denoised, _, _ = denoise_covariance_mp(S, 5)
    np.testing.assert_array_almost_equal(denoised, S)


def test_mp_invalid_method():
    try:
        denoise_covariance_mp(np.eye(3), 100, method="bad")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Unknown method" in str(e)


run_test("output_shape", test_mp_output_shape)
run_test("symmetry", test_mp_symmetry)
run_test("psd", test_mp_psd)
run_test("skip_q<=1", test_mp_skip_q_le_1)
run_test("invalid_method", test_mp_invalid_method)

# --- Ledoit-Wolf ---
print("\nTestLedoitWolf:")


def test_lw_shrinkage_range():
    X, S = _get_X_S(200, 10)
    shrinkage, _ = _ledoit_wolf_shrinkage(X, S)
    assert 0.0 <= shrinkage <= 1.0


def test_lw_shrunk_psd():
    X, S = _get_X_S(200, 10)
    _, shrunk = _ledoit_wolf_shrinkage(X, S)
    eigenvalues = np.linalg.eigvalsh(shrunk)
    assert np.all(eigenvalues >= -1e-10)


def test_cc_shrinkage():
    X, S = _get_X_S(200, 10)
    shrinkage, _ = _shrinkage_constant_correlation(X, S)
    assert 0.0 <= shrinkage <= 1.0


run_test("shrinkage_in_range", test_lw_shrinkage_range)
run_test("shrunk_psd", test_lw_shrunk_psd)
run_test("constant_correlation", test_cc_shrinkage)

# --- Full pipeline ---
print("\nTestEstimateFactorCovariance:")


def test_basic_cov():
    returns = _random_returns()
    result = estimate_factor_covariance(returns)
    assert isinstance(result, CovarianceResult)
    assert result.n_factors == 10
    assert result.cov.shape == (10, 10)
    assert result.corr.shape == (10, 10)
    assert len(result.eigenvalues) == 10


def test_cov_symmetry():
    returns = _random_returns()
    result = estimate_factor_covariance(returns)
    np.testing.assert_array_almost_equal(result.cov.values, result.cov.values.T, decimal=10)


def test_corr_diagonal():
    returns = _random_returns()
    result = estimate_factor_covariance(returns)
    np.testing.assert_array_almost_equal(np.diag(result.corr.values), np.ones(10), decimal=10)


def test_psd():
    returns = _random_returns()
    result = estimate_factor_covariance(returns)
    eigenvalues = np.linalg.eigvalsh(result.cov.values)
    assert np.all(eigenvalues >= -1e-10)


def test_shrinkage_range():
    returns = _random_returns()
    result = estimate_factor_covariance(returns)
    assert 0.0 <= result.shrinkage <= 1.0


def test_n_effective():
    returns = _random_returns()
    result = estimate_factor_covariance(returns)
    assert 1.0 <= result.n_effective <= 10.0


def test_with_icir():
    returns = _random_returns()
    weights = _weights()
    result = estimate_factor_covariance(returns, icir_weights=weights)
    assert result.sharpe is not None
    assert result.mean_ic is not None
    assert isinstance(result.sharpe, float)


def test_no_denoise():
    returns = _random_returns(200, 8)
    result = estimate_factor_covariance(returns, denoise=False)
    assert isinstance(result, CovarianceResult)
    assert result.n_factors == 8


def test_insufficient_samples():
    returns = _random_returns(10, 10)
    try:
        estimate_factor_covariance(returns, min_samples=30)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Insufficient samples" in str(e)


def test_single_factor():
    returns = pd.DataFrame({"alpha_000": np.random.randn(100) * 0.02})
    try:
        estimate_factor_covariance(returns)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Need >= 2 factors" in str(e)


run_test("basic", test_basic_cov)
run_test("symmetry", test_cov_symmetry)
run_test("corr_diagonal", test_corr_diagonal)
run_test("psd", test_psd)
run_test("shrinkage_range", test_shrinkage_range)
run_test("n_effective", test_n_effective)
run_test("with_icir", test_with_icir)
run_test("no_denoise", test_no_denoise)
run_test("insufficient_samples", test_insufficient_samples)
run_test("single_factor", test_single_factor)

# --- Rolling covariance ---
print("\nTestRollingCovariance:")


def test_rolling_output():
    returns = _random_returns(300, 8)
    results = rolling_covariance(returns, window=60, step=50)
    assert len(results) > 0
    for _, result in results:
        assert isinstance(result, CovarianceResult)


def test_factor_sharpe():
    returns = _random_returns(300, 8)
    weights = _weights(8)
    sharpe_series = factor_portfolio_sharpe(returns, weights, window=60)
    assert isinstance(sharpe_series, pd.Series)
    assert len(sharpe_series) > 0
    assert sharpe_series.name == "factor_sharpe"


run_test("rolling_output", test_rolling_output)
run_test("factor_sharpe", test_factor_sharpe)

# --- Portfolio Sharpe ---
print("\nTestPortfolioSharpe:")


def test_sharpe_finite():
    returns = _random_returns(500, 5)
    weights = _weights(5)
    cov = returns.cov().values * 252
    sharpe, mean_ic = _compute_portfolio_sharpe(cov, list(returns.columns), returns, weights)
    assert np.isfinite(sharpe)
    assert np.isfinite(mean_ic)


def test_zero_weights():
    n = 5
    cov = np.eye(n)
    factor_names = [f"f{i}" for i in range(n)]
    factor_returns = pd.DataFrame(np.random.randn(100, n), columns=factor_names)
    weights = pd.Series(np.zeros(n), index=factor_names)
    sharpe, _ = _compute_portfolio_sharpe(cov, factor_names, factor_returns, weights)
    assert sharpe == 0.0


run_test("sharpe_finite", test_sharpe_finite)
run_test("zero_weights", test_zero_weights)

# --- Nearest PSD ---
print("\nTestNearestPSD:")


def test_nearest_psd_already():
    A = np.array([[2.0, 0.5], [0.5, 1.0]])
    B = _nearest_psd(A)
    np.testing.assert_array_almost_equal(A, B, decimal=10)


def test_nearest_psd_symmetry():
    A = np.array([[2.0, 0.3], [0.1, 1.0]])
    B = _nearest_psd(A)
    np.testing.assert_array_almost_equal(B, B.T, decimal=10)


run_test("already_psd", test_nearest_psd_already)
run_test("symmetry", test_nearest_psd_symmetry)

# --- Integration ---
print("\nTestIntegration:")


def test_full_pipeline():
    returns = _random_returns(500, 10)
    result = estimate_factor_covariance(returns)
    assert result.n_factors == 10
    assert result.shrinkage > 0

    weights = _weights(10)
    rolling = rolling_covariance(returns, window=120, step=50, icir_weights=weights)
    assert len(rolling) > 0

    sharpe_series = factor_portfolio_sharpe(returns, weights, window=120)
    assert len(sharpe_series) > 0


run_test("full_pipeline", test_full_pipeline)

# --- Summary ---
print(f"\n{'=' * 50}")
print(f"Results: {passed} passed, {failed} failed out of {passed + failed}")
print(f"{'=' * 50}")

if failed > 0:
    sys.exit(1)
