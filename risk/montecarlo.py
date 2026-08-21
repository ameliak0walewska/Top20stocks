"""QuantLib path generation for the portfolio risk simulation - S5 of
quantlib-risk-engine-spec.md.

Multivariate GBM: r_t = (mu - 0.5*sigma^2)*dt + sigma*sqrt(dt)*z, where z is
a correlated *unit-variance* standard normal (see covariance.py's docstring
for why the correlated sqrt is built from the correlation matrix, not the
raw covariance). Portfolio return is the weight-aggregated sum of per-asset
cumulative log returns over the horizon - the standard short-horizon
approximation for this class of risk engine.
"""

import math
from dataclasses import dataclass

import numpy as np
import QuantLib as ql

from .covariance import correlated_sqrt


@dataclass
class PathResult:
    paths: np.ndarray            # (n_paths, n_steps) portfolio cumulative log-return path
    terminal_returns: np.ndarray  # (n_paths,) portfolio log return over the full horizon
    salvaged: bool
    salvage_delta: float
    n_paths: int
    n_steps: int
    generator: str
    seed: int


def _make_gaussian_generator(generator: str, dim: int, seed: int):
    if generator == "sobol":
        useg = ql.UniformLowDiscrepancySequenceGenerator(dim, seed)
        return ql.GaussianLowDiscrepancySequenceGenerator(useg)
    elif generator == "mersenne":
        urng = ql.UniformRandomGenerator(seed)
        ursg = ql.UniformRandomSequenceGenerator(dim, urng)
        return ql.GaussianRandomSequenceGenerator(ursg)
    raise ValueError(f"unknown generator {generator!r} (expected 'sobol' or 'mersenne')")


def simulate_portfolio(
    weights: np.ndarray,
    Sigma_ann: np.ndarray,
    horizon_days: int,
    n_paths: int,
    seed: int = 42,
    generator: str = "mersenne",
    antithetic: bool = True,
    mu: np.ndarray = None,
) -> PathResult:
    n_assets = len(weights)
    sigma_ann = np.sqrt(np.diag(Sigma_ann))
    Sigma_daily = Sigma_ann / 252.0
    L, salvaged, salvage_delta = correlated_sqrt(Sigma_daily)

    dt = 1.0 / 252.0
    n_steps = horizon_days
    dim = n_assets * n_steps
    mu_vec = np.zeros(n_assets) if mu is None else np.asarray(mu, dtype=float)

    drift = (mu_vec - 0.5 * sigma_ann ** 2) * dt          # (n_assets,)
    vol_step = sigma_ann * math.sqrt(dt)                   # (n_assets,)

    n_draws = math.ceil(n_paths / 2) if antithetic else n_paths
    gsg = _make_gaussian_generator(generator, dim, seed)

    portfolio_paths = []
    for _ in range(n_draws):
        seq = np.array(gsg.nextSequence().value()).reshape(n_steps, n_assets)
        z_corr = seq @ L.T                                 # (n_steps, n_assets)

        asset_step_returns = drift + vol_step * z_corr
        asset_cum = np.cumsum(asset_step_returns, axis=0)  # (n_steps, n_assets)
        portfolio_paths.append(asset_cum @ weights)         # (n_steps,)

        if antithetic:
            asset_step_returns_a = drift - vol_step * z_corr
            asset_cum_a = np.cumsum(asset_step_returns_a, axis=0)
            portfolio_paths.append(asset_cum_a @ weights)

    paths = np.array(portfolio_paths)[:n_paths]
    terminal_returns = paths[:, -1]

    return PathResult(
        paths=paths,
        terminal_returns=terminal_returns,
        salvaged=salvaged,
        salvage_delta=salvage_delta,
        n_paths=paths.shape[0],
        n_steps=n_steps,
        generator=generator,
        seed=seed,
    )
