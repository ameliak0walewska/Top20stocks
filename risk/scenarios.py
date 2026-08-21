"""Deterministic stress scenarios - S8 of quantlib-risk-engine-spec.md.
Separate from the MC engine; logged but not acted on initially.

Each scenario revalues the portfolio's return under a fixed shock to either
asset returns or the covariance matrix. The correlation scenarios matter
most - they answer "what happens when diversification stops working."
"""

import numpy as np


def _shocked_correlation(Sigma: np.ndarray, target_corr: float) -> np.ndarray:
    std = np.sqrt(np.diag(Sigma))
    n = len(std)
    corr = np.full((n, n), target_corr)
    np.fill_diagonal(corr, 1.0)
    return corr * np.outer(std, std)


def broad_selloff(weights, Sigma, returns_row_shock=-0.10, target_corr=0.90):
    shocked_Sigma = _shocked_correlation(Sigma, target_corr)
    portfolio_return = float(np.sum(weights) * returns_row_shock)
    return {"portfolio_return": portfolio_return, "shocked_correlation": target_corr}


def vol_spike(weights, Sigma, multiplier=2.0):
    shocked_Sigma = Sigma * (multiplier ** 2)
    sigma_p_before = float(np.sqrt(weights @ Sigma @ weights))
    sigma_p_after = float(np.sqrt(weights @ shocked_Sigma @ weights))
    return {"sigma_p_before": sigma_p_before, "sigma_p_after": sigma_p_after}


def correlation_breakdown(weights, Sigma, target_corr=0.95):
    shocked_Sigma = _shocked_correlation(Sigma, target_corr)
    sigma_p_before = float(np.sqrt(weights @ Sigma @ weights))
    sigma_p_after = float(np.sqrt(weights @ shocked_Sigma @ weights))
    return {"sigma_p_before": sigma_p_before, "sigma_p_after": sigma_p_after, "shocked_correlation": target_corr}


def sector_rotation(weights, sector_of, largest_sector_shock=-0.15, other_shock=0.02):
    sector_totals = {}
    for ticker, w in weights.items():
        sector_totals[sector_of.get(ticker, "Unknown")] = sector_totals.get(sector_of.get(ticker, "Unknown"), 0.0) + w
    if not sector_totals:
        return {"portfolio_return": 0.0, "largest_sector": None}
    largest_sector = max(sector_totals, key=sector_totals.get)

    portfolio_return = 0.0
    for ticker, w in weights.items():
        shock = largest_sector_shock if sector_of.get(ticker) == largest_sector else other_shock
        portfolio_return += w * shock

    return {"portfolio_return": float(portfolio_return), "largest_sector": largest_sector, "largest_sector_weight": sector_totals[largest_sector]}


def historical_replay(weights: dict, close_prices: "pd.DataFrame", start_date, n_days: int = 20):
    """Replay n_days of actual historical returns starting at start_date.
    Tickers without history over that window are excluded and flagged
    rather than substituted (S8)."""
    window = close_prices.loc[close_prices.index >= start_date].iloc[: n_days + 1]
    if len(window) < 2:
        return {"portfolio_return": None, "excluded": list(weights.keys()), "reason": "no bars in window"}

    log_ret = np.log(window / window.shift(1)).iloc[1:]

    available = [t for t in weights if t in log_ret.columns and not log_ret[t].isna().any()]
    excluded = [t for t in weights if t not in available]

    if not available:
        return {"portfolio_return": None, "excluded": excluded, "reason": "no tickers with full history in window"}

    w = np.array([weights[t] for t in available])
    w = w / w.sum()  # renormalise over the tickers actually replayed
    cum_ret = np.exp(log_ret[available].sum(axis=0)) - 1
    portfolio_return = float(np.dot(w, cum_ret.values))

    return {"portfolio_return": portfolio_return, "excluded": excluded, "n_days_used": len(log_ret)}


def run_all(weights_dict, Sigma: np.ndarray, tickers: list, sector_of: dict = None, close_prices=None):
    """weights_dict: {ticker: weight}. Sigma indexed in the same order as `tickers`."""
    w = np.array([weights_dict.get(t, 0.0) for t in tickers])

    results = {
        "broad_selloff": broad_selloff(w, Sigma),
        "vol_spike": vol_spike(w, Sigma),
        "correlation_breakdown": correlation_breakdown(w, Sigma),
    }
    if sector_of is not None:
        results["sector_rotation"] = sector_rotation(weights_dict, sector_of)
    if close_prices is not None:
        results["historical_2020_03"] = historical_replay(weights_dict, close_prices, "2020-02-19", n_days=20)
        # worst 20-day window in 2022, found by scanning realised 20-day portfolio returns
        w_full = np.array([weights_dict.get(t, 0.0) for t in close_prices.columns])
        w_full = w_full / w_full.sum() if w_full.sum() > 0 else w_full
        year = close_prices.loc[(close_prices.index >= "2022-01-01") & (close_prices.index < "2023-01-01")]
        if len(year) > 20:
            log_ret = np.log(year / year.shift(1)).dropna()
            rolling_20d = log_ret.rolling(20).sum().dropna()
            portfolio_rolling = rolling_20d @ w_full
            worst_end = portfolio_rolling.idxmin()
            worst_start = year.index[year.index.get_loc(worst_end) - 20]
            results["historical_2022_drawdown"] = historical_replay(weights_dict, close_prices, worst_start, n_days=20)
        else:
            results["historical_2022_drawdown"] = {"portfolio_return": None, "reason": "insufficient 2022 bars"}

    return results
