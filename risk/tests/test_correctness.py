"""Correctness validation required before the MC engine drives sizing -
quantlib-risk-engine-spec.md S9. Run with: pytest risk/tests/test_correctness.py -v
"""

import numpy as np
import pytest

from risk.covariance import correlated_sqrt
from risk.measures import (
    conditional_value_at_risk,
    normal_cvar_closed_form,
    portfolio_vol_from_paths,
)
from risk.montecarlo import simulate_portfolio


def _random_covariance(n_assets, seed=0, scale=0.02):
    rng = np.random.default_rng(seed)
    A = rng.standard_normal((n_assets, n_assets)) * scale
    Sigma_daily = A @ A.T + np.eye(n_assets) * 1e-4
    return Sigma_daily * 252


@pytest.mark.parametrize("generator", ["mersenne", "sobol"])
def test_analytic_agreement(generator):
    """The single most important test (S9): with mu=0, simulated portfolio
    volatility must match sqrt(w.T @ Sigma @ w) within MC error. Validates
    the correlation structure end to end."""
    weights = np.array([0.3, 0.25, 0.2, 0.15, 0.1])
    Sigma_ann = _random_covariance(5, seed=0)
    analytic_sigma_p = np.sqrt(weights @ Sigma_ann @ weights)

    res = simulate_portfolio(weights, Sigma_ann, horizon_days=5, n_paths=100_000, seed=42, generator=generator)
    sim_sigma_p = portfolio_vol_from_paths(res.terminal_returns, horizon_days=5)

    assert abs(sim_sigma_p - analytic_sigma_p) / analytic_sigma_p < 0.01


def test_normal_case_cvar_closed_form():
    """For N(0, sigma^2), CVaR_95 = sigma * phi(z_95) / 0.05 (S9). Cancel the
    Ito drift term (mu = 0.5*sigma^2) so the terminal distribution is exactly
    zero-mean normal, matching the closed form's assumption."""
    sigma_ann = 0.30
    horizon_days = 5
    T = horizon_days / 252
    Sigma_ann = np.array([[sigma_ann ** 2]])
    weights = np.array([1.0])
    mu = np.array([0.5 * sigma_ann ** 2])

    sigma_h = sigma_ann * np.sqrt(T)
    closed_form = normal_cvar_closed_form(sigma_h, alpha=0.95)

    res = simulate_portfolio(weights, Sigma_ann, horizon_days=horizon_days, n_paths=500_000, seed=42, mu=mu)
    simulated = conditional_value_at_risk(res.terminal_returns, alpha=0.95)

    assert abs(simulated - closed_form) / closed_form < 0.01


def test_sobol_vs_mersenne_agree():
    """Both generators must converge to the same value; disagreement beyond
    MC error indicates a dimension-ordering or seeding bug (S9)."""
    weights = np.array([0.5, 0.3, 0.2])
    Sigma_ann = _random_covariance(3, seed=1)

    cvars = {}
    for generator in ("mersenne", "sobol"):
        res = simulate_portfolio(weights, Sigma_ann, horizon_days=5, n_paths=200_000, seed=42, generator=generator)
        cvars[generator] = conditional_value_at_risk(res.terminal_returns)

    assert abs(cvars["mersenne"] - cvars["sobol"]) / cvars["mersenne"] < 0.03


def test_determinism():
    weights = np.array([0.4, 0.35, 0.25])
    Sigma_ann = _random_covariance(3, seed=2)

    for generator in ("mersenne", "sobol"):
        res1 = simulate_portfolio(weights, Sigma_ann, horizon_days=5, n_paths=2000, seed=42, generator=generator)
        res2 = simulate_portfolio(weights, Sigma_ann, horizon_days=5, n_paths=2000, seed=42, generator=generator)
        assert np.array_equal(res1.terminal_returns, res2.terminal_returns)


def test_convergence_reduces_standard_error():
    """CVaR standard error should shrink as n_paths grows (S9) - checked by
    comparing spread across independent seeds at a small vs. large n_paths,
    since a single run's CVaR is itself a random variable."""
    weights = np.array([0.5, 0.5])
    Sigma_ann = _random_covariance(2, seed=3)

    def spread(n_paths, n_seeds=6):
        vals = [
            conditional_value_at_risk(
                simulate_portfolio(weights, Sigma_ann, horizon_days=5, n_paths=n_paths, seed=s).terminal_returns
            )
            for s in range(n_seeds)
        ]
        return np.std(vals)

    small_spread = spread(1_000)
    large_spread = spread(50_000)
    assert large_spread < small_spread


def test_degenerate_single_asset():
    weights = np.array([1.0])
    Sigma_ann = np.array([[0.04]])
    res = simulate_portfolio(weights, Sigma_ann, horizon_days=5, n_paths=1000, seed=1)
    assert np.isfinite(res.terminal_returns).all()
    assert not res.salvaged


def test_degenerate_perfectly_correlated():
    Sigma_daily = np.array([[0.0004, 0.0004], [0.0004, 0.0004]])
    L, salvaged, delta = correlated_sqrt(Sigma_daily)
    assert np.isfinite(L).all()
    res = simulate_portfolio(np.array([0.5, 0.5]), Sigma_daily * 252, horizon_days=5, n_paths=1000, seed=1)
    assert np.isfinite(res.terminal_returns).all()


def test_degenerate_non_psd_matrix_is_salvaged():
    """A correlation matrix with inconsistent pairwise correlations (not PSD)
    must be salvaged, not crash or silently produce garbage (S9)."""
    bad_corr = np.array([
        [1.0, 0.9, -0.9],
        [0.9, 1.0, 0.9],
        [-0.9, 0.9, 1.0],
    ])
    assert np.linalg.eigvalsh(bad_corr).min() < 0  # confirm it's genuinely non-PSD
    Sigma_daily = bad_corr * 0.0004

    L, salvaged, delta = correlated_sqrt(Sigma_daily)
    assert salvaged
    assert delta > 0
    assert np.isfinite(L).all()

    res = simulate_portfolio(np.array([0.4, 0.3, 0.3]), Sigma_daily * 252, horizon_days=5, n_paths=1000, seed=1)
    assert np.isfinite(res.terminal_returns).all()
