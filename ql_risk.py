"""
ql_risk.py

QuantLib-based risk utilities for the Top20stocks pipeline - deliberately
NOT touching options pricing. Two genuine, non-derivatives uses of
QuantLib for a long-only equity portfolio:

    1. Calendar-accurate trading-day counts (real NYSE holidays, not a
       flat "assume 252/year"), used to annualize volatility and the
       covariance matrix properly.
    2. Portfolio Value-at-Risk / Expected Shortfall computed by
       QuantLib's own risk-statistics engine (ql.RiskStatistics), fed the
       *realized* portfolio daily return series - today's target weights
       applied retroactively to each day's actual per-stock returns -
       rather than a hand-rolled z-score formula.

Install (on the machine that actually runs position_sizing.py):
    pip install QuantLib

Note: this couldn't be test-run in the environment that wrote it (no
network access there to install QuantLib), so it's written directly
against QuantLib's documented Python API. If `portfolio_var()` throws a
signature error the first time you run it, paste the traceback back and
it'll get a fast fix - same as everything else in this pipeline.
"""

from datetime import timedelta

import numpy as np
import QuantLib as ql


# ---------------------------------------------------------------------------
# 1. Calendar-accurate annualization
# ---------------------------------------------------------------------------

def _to_ql_date(d):
    return ql.Date(d.day, d.month, d.year)


def nyse_trading_days(start_date, end_date):
    """
    Actual number of NYSE trading sessions between two datetime.date (or
    datetime) objects - accounts for real NYSE holidays as well as
    weekends, instead of assuming a flat 252/year.
    """
    calendar = ql.UnitedStates(ql.UnitedStates.NYSE)
    d1 = _to_ql_date(start_date)
    d2 = _to_ql_date(end_date)
    return calendar.businessDaysBetween(d1, d2)


def trading_days_per_year(as_of_date, lookback_days=365):
    """
    Trailing-year NYSE session count as of a given date, for annualizing
    volatility/covariance instead of hardcoding 252. Typically comes out
    to ~250-252 - the point isn't a big number, it's using the real
    calendar instead of a guess.
    """
    return nyse_trading_days(as_of_date - timedelta(days=lookback_days), as_of_date)


# ---------------------------------------------------------------------------
# 2. Portfolio VaR / Expected Shortfall via QuantLib's risk-statistics engine
# ---------------------------------------------------------------------------

def portfolio_var(daily_returns, confidence=0.95):
    """
    1-day Value-at-Risk and Expected Shortfall (CVaR) on a series of
    portfolio daily returns, via QuantLib's RiskStatistics accumulator
    (ql/math/statistics/riskstatistics.hpp) - a Gaussian parametric
    estimate built from QuantLib's own mean/variance + inverse-normal
    machinery, not a from-scratch formula.

    `daily_returns` - any 1-D iterable of daily fractional returns
    (e.g. today's target weights applied retroactively to each day's
    actual per-stock returns over the lookback window).

    Returns fractional (not dollar) figures - multiply by portfolio
    value for a dollar VaR/ES.
    """
    returns = np.asarray(daily_returns, dtype=float)
    returns = returns[~np.isnan(returns)]
    if len(returns) < 20:
        raise ValueError("need at least ~20 daily returns for a meaningful VaR estimate")

    stats = ql.RiskStatistics()
    for r in returns:
        stats.add(float(r))

    var = stats.valueAtRisk(confidence)
    es = stats.expectedShortfall(confidence)

    return {
        "confidence": confidence,
        "n_days": len(returns),
        "var_pct": float(var),
        "expected_shortfall_pct": float(es),
    }
