"""
quant_risk_toolkit.py

A small demo showing how QuantLib fits into a stock-prediction pipeline —
NOT for predicting prices (that's your model's job), but for the three
things it's actually built for:

    1. Volatility        -> how risky is this stock right now?
    2. Option pricing     -> fair-value / Greeks, if you trade options or
                              want a market-implied sanity check
    3. Risk metrics       -> how much could a position lose? (VaR)
    4. Position sizing    -> given your model's signal + the risk above,
                              how many shares should you actually buy?

Your prediction model plugs in at the `predicted_edge` / `confidence`
inputs to `position_size()` — everything upstream of that (the actual
"which stock, which direction" call) stays in your existing code.

Install (on your Mac, not needed to read this file):
    pip install QuantLib pandas numpy
"""

import math
import numpy as np
import QuantLib as ql


# ---------------------------------------------------------------------------
# 1. VOLATILITY
# ---------------------------------------------------------------------------

def historical_volatility(prices, trading_days=252):
    """
    Annualized realized volatility from a list/array of daily close prices.
    """
    prices = np.asarray(prices, dtype=float)
    log_returns = np.diff(np.log(prices))
    daily_vol = np.std(log_returns, ddof=1)
    return daily_vol * math.sqrt(trading_days)


# ---------------------------------------------------------------------------
# 2. OPTION PRICING (Black-Scholes via QuantLib) + GREEKS
# ---------------------------------------------------------------------------

def price_option(spot, strike, volatility, risk_free_rate, dividend_yield,
                  expiry_date, valuation_date=None, option_type="call"):
    """
    Prices a European option with QuantLib's analytic Black-Scholes engine
    and returns fair value + Greeks.
    """
    if valuation_date is None:
        valuation_date = ql.Date.todaysDate()
    ql.Settings.instance().evaluationDate = valuation_date

    calendar = ql.UnitedStates(ql.UnitedStates.NYSE)
    day_count = ql.Actual365Fixed()

    spot_handle = ql.QuoteHandle(ql.SimpleQuote(spot))
    rate_handle = ql.YieldTermStructureHandle(
        ql.FlatForward(valuation_date, risk_free_rate, day_count))
    div_handle = ql.YieldTermStructureHandle(
        ql.FlatForward(valuation_date, dividend_yield, day_count))
    vol_handle = ql.BlackVolTermStructureHandle(
        ql.BlackConstantVol(valuation_date, calendar, volatility, day_count))

    process = ql.BlackScholesMertonProcess(
        spot_handle, div_handle, rate_handle, vol_handle)

    payoff = ql.PlainVanillaPayoff(
        ql.Option.Call if option_type == "call" else ql.Option.Put, strike)
    exercise = ql.EuropeanExercise(expiry_date)
    option = ql.VanillaOption(payoff, exercise)
    option.setPricingEngine(ql.AnalyticEuropeanEngine(process))

    return {
        "npv": option.NPV(),
        "delta": option.delta(),
        "gamma": option.gamma(),
        "vega": option.vega(),
        "theta": option.theta(),
        "rho": option.rho(),
    }


# ---------------------------------------------------------------------------
# 3. RISK METRICS — parametric Value-at-Risk
# ---------------------------------------------------------------------------

_Z_SCORES = {0.90: 1.2816, 0.95: 1.6449, 0.99: 2.3263}


def parametric_var(position_value, annual_volatility, confidence=0.95,
                    horizon_days=1, trading_days=252):
    """
    Parametric (variance-covariance) VaR.
    """
    z = _Z_SCORES.get(confidence)
    if z is None:
        raise ValueError(f"confidence must be one of {list(_Z_SCORES)}")

    horizon_vol = annual_volatility * math.sqrt(horizon_days / trading_days)
    return position_value * z * horizon_vol


# ---------------------------------------------------------------------------
# 4. POSITION SIZING — volatility-scaled, fed by your model's signal
# ---------------------------------------------------------------------------

def position_size(account_equity, price, annual_volatility,
                   risk_per_trade_pct=0.01, confidence=0.95,
                   predicted_edge=None, max_position_pct=0.20):
    """
    Volatility-scaled position sizing, optionally weighted by your model's
    predicted_edge (conviction score).
    """
    z = _Z_SCORES.get(confidence)
    if z is None:
        raise ValueError(f"confidence must be one of {list(_Z_SCORES)}")

    risk_budget = account_equity * risk_per_trade_pct
    daily_vol = annual_volatility / math.sqrt(252)
    loss_per_share_at_confidence = price * z * daily_vol

    shares = risk_budget / loss_per_share_at_confidence

    if predicted_edge is not None:
        conviction = max(0.0, min(1.0, abs(predicted_edge)))
        shares *= conviction

    max_shares = (account_equity * max_position_pct) / price
    shares = min(shares, max_shares)

    return math.floor(shares)


# ---------------------------------------------------------------------------
# DEMO
# ---------------------------------------------------------------------------

def _demo():
    rng = np.random.default_rng(7)
    days = 120
    synthetic_prices = 150 * np.exp(np.cumsum(rng.normal(0.0004, 0.018, days)))
    current_price = synthetic_prices[-1]
    predicted_edge = 0.6

    account_equity = 50_000

    print(f"Simulated last price: ${current_price:.2f}")

    vol = historical_volatility(synthetic_prices)
    print(f"Annualized realized volatility: {vol:.1%}")

    today = ql.Date.todaysDate()
    expiry = today + ql.Period(30, ql.Days)
    option_result = price_option(
        spot=current_price,
        strike=round(current_price),
        volatility=vol,
        risk_free_rate=0.04,
        dividend_yield=0.01,
        expiry_date=expiry,
        valuation_date=today,
        option_type="call",
    )
    print(f"30d ATM call fair value: ${option_result['npv']:.2f} "
          f"(delta={option_result['delta']:.2f}, vega={option_result['vega']:.2f})")

    hypothetical_position_value = 10_000
    var_95 = parametric_var(hypothetical_position_value, vol, confidence=0.95)
    print(f"1-day 95% VaR on a ${hypothetical_position_value:,} position: "
          f"${var_95:,.2f}")

    shares = position_size(
        account_equity=account_equity,
        price=current_price,
        annual_volatility=vol,
        risk_per_trade_pct=0.01,
        predicted_edge=predicted_edge,
    )
    print(f"Suggested position size: {shares} shares "
          f"(~${shares * current_price:,.2f}, "
          f"{shares * current_price / account_equity:.1%} of equity)")


if __name__ == "__main__":
    _demo()
