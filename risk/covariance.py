"""Ledoit-Wolf covariance estimation (sklearn - QuantLib has no shrinkage
estimator) plus a QuantLib pseudo-sqrt for correlated path generation.

Implementation note on what gets passed to ql.pseudoSqrt: quantlib-risk-engine-spec.md
S4 says to build it from Sigma_daily directly, but S5.2's per-step formula
`sigma * sqrt(dt) * z` treats `z` as a *unit-variance* correlated normal and
applies each asset's own annualised sigma separately. Feeding pseudoSqrt the
raw covariance (rather than the correlation matrix) would double-scale the
variance once S5.2's sigma*sqrt(dt) is applied on top. This module builds
the square root from the correlation matrix instead, so z ends up unit
variance and S5.2's formula is applied exactly once - self-consistent, and
required for the S9 analytic-agreement test (simulated sigma_p must match
sqrt(w.T @ Sigma @ w)) to actually pass.
"""

import numpy as np
import QuantLib as ql
from sklearn.covariance import LedoitWolf


def estimate_covariance(returns_matrix: np.ndarray):
    """returns_matrix: (n_obs, n_assets) daily log returns.
    Returns (Sigma_daily, Sigma_ann), both (n_assets, n_assets)."""
    Sigma_daily = LedoitWolf().fit(returns_matrix).covariance_
    Sigma_ann = Sigma_daily * 252
    return Sigma_daily, Sigma_ann


def correlated_sqrt(Sigma_daily: np.ndarray, salvage_log_threshold: float = 1e-6):
    """Cholesky-like square root L (L @ L.T ~= Corr) of the correlation matrix
    implied by Sigma_daily, via QuantLib's pseudoSqrt with spectral salvaging
    (floors negative eigenvalues at zero for near-PSD matrices - this happens
    occasionally with real data; without it the run crashes).

    Returns (L, salvaged: bool, salvage_delta: float) - salvage_delta is the
    max absolute change pseudoSqrt made to the correlation matrix, for
    logging when it materially altered the input (a data-quality signal).
    """
    n = Sigma_daily.shape[0]
    std = np.sqrt(np.diag(Sigma_daily))
    corr = Sigma_daily / np.outer(std, std)
    corr = np.clip(corr, -1.0, 1.0)
    np.fill_diagonal(corr, 1.0)

    ql_matrix = ql.Matrix(n, n)
    for i in range(n):
        for j in range(n):
            ql_matrix[i][j] = float(corr[i, j])

    L_ql = ql.pseudoSqrt(ql_matrix, ql.SalvagingAlgorithm.Spectral)
    L = np.array([[L_ql[i][j] for j in range(n)] for i in range(n)])

    reconstructed = L @ L.T
    salvage_delta = float(np.max(np.abs(reconstructed - corr)))
    salvaged = salvage_delta > salvage_log_threshold

    return L, salvaged, salvage_delta
