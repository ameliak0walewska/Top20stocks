"""VaR, CVaR, portfolio vol, and drawdown from simulated paths - S6 of
quantlib-risk-engine-spec.md."""

import numpy as np


def value_at_risk(terminal_returns: np.ndarray, alpha: float = 0.95) -> float:
    losses = -terminal_returns
    return float(np.percentile(losses, alpha * 100))


def conditional_value_at_risk(terminal_returns: np.ndarray, alpha: float = 0.95) -> float:
    """CVaR / expected shortfall: mean loss in the worst (1-alpha) fraction of paths.
    Preferred over VaR - it's coherent (VaR fails subadditivity) and responds
    to tail shape rather than a single quantile."""
    losses = -terminal_returns
    var = np.percentile(losses, alpha * 100)
    tail = losses[losses >= var]
    return float(tail.mean()) if len(tail) else float(var)


def annualize(value: float, horizon_days: int) -> float:
    return value * np.sqrt(252.0 / horizon_days)


def portfolio_vol_from_paths(terminal_returns: np.ndarray, horizon_days: int) -> float:
    """Simulated portfolio volatility, annualised - must agree closely with
    sqrt(w.T @ Sigma @ w); a divergence is a bug, not a finding (S6, S9)."""
    return float(np.std(terminal_returns, ddof=1) * np.sqrt(252.0 / horizon_days))


def drawdown_stats(paths: np.ndarray) -> dict:
    """paths: (n_paths, n_steps) cumulative log-return paths, one row per
    simulated path. Returns max/mean/p95 max-drawdown across paths (all
    logged only, per S6 - not used for sizing)."""
    n_paths = paths.shape[0]
    values = np.concatenate([np.ones((n_paths, 1)), np.exp(paths)], axis=1)
    running_max = np.maximum.accumulate(values, axis=1)
    drawdowns = (values - running_max) / running_max
    max_dd_per_path = drawdowns.min(axis=1)  # most negative excursion per path

    return {
        "max_drawdown": float(max_dd_per_path.min()),
        "mean_drawdown": float(max_dd_per_path.mean()),
        # "95th percentile" of drawdown severity = the 5th percentile of the
        # (negative) per-path max drawdowns - the threshold the worst 5% of
        # paths breach.
        "p95_drawdown": float(np.percentile(max_dd_per_path, 5)),
    }


def normal_cvar_closed_form(sigma: float, alpha: float = 0.95) -> float:
    """Closed-form CVaR for N(0, sigma^2): sigma * phi(z_alpha) / (1 - alpha).
    Used only to validate the MC engine (S9) - not part of the sizing path."""
    from scipy.stats import norm

    z_alpha = norm.ppf(alpha)
    return sigma * norm.pdf(z_alpha) / (1 - alpha)
