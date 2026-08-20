"""
Position sizing pipeline — implements position-sizing-spec.md sections 1-7.

Turns a weekly ranked-stock CSV into target portfolio weights and writes an
output CSV with a `position_size` column (fraction of portfolio equity,
e.g. 0.095 = 9.5%) that trade_from_csv.py consumes directly.

Weekly input CSV format - exactly 20 rows. Required columns (case/spacing
insensitive, e.g. "Risk Index" == "risk_index"): ticker, ranking, regime,
risk_index, volatility_index, sentiment_index. Extra descriptive columns
(Sector, Why Included, Valuation, Bear Case, ...) are tolerated and ignored.

`regime` in this feed is a PER-STOCK trend flag (that stock's close vs its
own 200-day SMA), not a single market-wide flag - it is carried through to
the output CSV but NOT used to scale position sizes (see Config.apply_regime_scaling,
off by default). There is no timestamp column, so staleness is detected via
content hash + file modification time instead (S2).

Pipeline (spec section references in comments):
    1. Validate the CSV (S2) - fail closed, exit non-zero on any problem.
    2. Pull ~300 days of daily bars for all tickers + benchmark from Alpaca.
    3. Compute EWMA volatility per name (S3).
    4. Compute raw ranking/vol weights, optional risk/sentiment modifiers (S4).
    5. Apply position cap / floor / liquidity constraints (S5).
    6. Ledoit-Wolf portfolio covariance -> vol targeting + regime scaling (S6).
    7. Cross-check computed vol vs supplied volatility_index (S7, log only).
    8. Write output CSV + a full JSON run log (S9).

NOT implemented here: S8 order generation (that's trade_from_csv.py) and the
top-25 exit hysteresis, which the weekly CSV's fixed ranking-1..20 contract
cannot support (there is no rank-21..25 data to check a dropped name
against) - see conversation notes.

Setup: same ALPACA_API_KEY / ALPACA_SECRET_KEY in .env as trade_from_csv.py.

Usage:
    python position_sizing.py weekly_input.csv --output target_positions.csv
"""

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sklearn.covariance import LedoitWolf

from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

load_dotenv()

BASE_DIR = Path(__file__).parent
STATE_DIR = BASE_DIR / "state"
LOG_DIR = BASE_DIR / "logs"
STATE_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)
REGIME_STATE_PATH = STATE_DIR / "regime_state.json"
INPUT_HASH_STATE_PATH = STATE_DIR / "last_input_hash.json"

REQUIRED_COLUMNS = {"ticker", "ranking", "regime", "risk_index", "volatility_index", "sentiment_index"}


class ValidationError(Exception):
    pass


@dataclass
class Config:
    sigma_target: float = 0.15
    position_cap: float = 0.12
    position_floor: float = 0.015
    min_trade_abs: float = 25.0          # consumed downstream by trade_from_csv.py
    no_trade_band: float = 0.20          # consumed downstream by trade_from_csv.py
    exit_rank: int = 25                  # NOT enforced here - see module docstring
    regime_off_scalar: float = 0.40
    ewma_halflife: int = 21
    vol_floor: float = 0.12
    vol_cap: float = 0.80
    liquidity_frac: float = 0.005
    use_risk_index: bool = False
    use_sentiment_index: bool = False
    apply_regime_scaling: bool = False   # off by default: this feed's Regime is per-stock, not market-wide - see S6.1 note
    benchmark: str = "SPY"
    lookback_days: int = 300
    cov_window: int = 250
    max_constraint_passes: int = 5
    regime_confirm_days: int = 3
    max_file_age_days: int = 3


# ---------------------------------------------------------------------------
# S2: load + validate
# ---------------------------------------------------------------------------

def load_weekly_csv(csv_path):
    df = pd.read_csv(csv_path)
    # Normalise "Risk Index" / "risk_index" / " risk_index " etc. to the same key.
    # Extra columns (Sector, Why Included, Valuation, Bear Case, ...) are tolerated and just ignored.
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValidationError(f"CSV missing required columns: {sorted(missing)}")
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["ranking"] = pd.to_numeric(df["ranking"], errors="coerce")
    for col in ("regime", "risk_index", "volatility_index", "sentiment_index"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def check_staleness(csv_path, cfg: Config):
    """Two independent guards, since there's no generated_at column (S2):
    the file's content must differ from the last processed run, AND its
    mtime must be recent. Either failing alone is not sufficient."""
    errors = []
    path = Path(csv_path)
    content = path.read_bytes()
    file_hash = hashlib.sha256(content).hexdigest()
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    age = datetime.now(timezone.utc) - mtime

    if age > timedelta(days=cfg.max_file_age_days):
        errors.append(f"input file was last modified {age.days} day(s) ago (> {cfg.max_file_age_days} day staleness limit)")

    prev = {}
    if INPUT_HASH_STATE_PATH.exists():
        try:
            prev = json.loads(INPUT_HASH_STATE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            prev = {}
    if prev.get("hash") == file_hash:
        errors.append("input file content hash matches the last processed run - file was not regenerated")

    return errors, file_hash, mtime


def validate(df, trading_client, cfg: Config):
    errors = []

    n = len(df)
    if n < 10:
        errors.append(f"row count {n} < 10 - aborting")
    elif n > 20:
        errors.append(f"row count {n} > 20 - expected exactly 20")
    elif n != 20:
        print(f"  WARNING: row count is {n}, expected exactly 20 - proceeding since it's within 10-19")

    dupes = df["ticker"][df["ticker"].duplicated()].unique().tolist()
    if dupes:
        errors.append(f"duplicate tickers: {dupes}")

    if df["ranking"].isna().any():
        errors.append("some ranking values are missing/non-numeric")
    else:
        rankings = sorted(int(r) for r in df["ranking"].tolist())
        if rankings != list(range(1, n + 1)):
            errors.append(f"rankings are not a strict 1..{n} sequence with no ties/gaps: got {rankings}")
        if n > 0 and (max(rankings) > 20 or min(rankings) < 1):
            errors.append("ranking values outside the 1-20 range")

    # NOTE: regime here is a per-stock trend flag (close vs that stock's own 200-day SMA),
    # not a single market-wide flag, so it is NOT required to be uniform across rows.
    # It is currently unused by the sizing pipeline (see run_pipeline) - validated only
    # for data quality, same as the other index columns.
    if df["regime"].isna().any():
        errors.append("regime column has missing/non-numeric values")
    elif not df["regime"].isin([0, 1]).all():
        bad = df.loc[~df["regime"].isin([0, 1]), "ticker"].tolist()
        errors.append(f"regime values must be 0 or 1: bad for {bad}")

    for col in ("risk_index", "volatility_index", "sentiment_index"):
        if df[col].isna().any():
            bad = df.loc[df[col].isna(), "ticker"].tolist()
            errors.append(f"{col} missing/non-numeric for: {bad}")

    for ticker in df["ticker"]:
        try:
            asset = trading_client.get_asset(ticker)
            if not asset.tradable:
                errors.append(f"{ticker} is not tradable")
        except Exception as e:
            errors.append(f"{ticker} failed Alpaca asset lookup: {e}")

    return errors


# ---------------------------------------------------------------------------
# Data layer: bars, EWMA vol, covariance, ADV
# ---------------------------------------------------------------------------

def fetch_daily_bars(data_client, symbols, lookback_days):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(lookback_days * 1.6) + 15)
    req = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
        feed=DataFeed.IEX,
    )
    bars = data_client.get_stock_bars(req).df
    if bars.empty:
        raise ValidationError("Alpaca returned no bar data for the requested symbols")
    return bars


def bars_to_frames(bars_df, symbols, lookback_days):
    close = bars_df["close"].unstack(level=0).tail(lookback_days)
    volume = bars_df["volume"].unstack(level=0).tail(lookback_days)

    missing = [s for s in symbols if s not in close.columns]
    if missing:
        raise ValidationError(f"missing bars entirely for: {missing}")

    gapped = [s for s in symbols if close[s].isna().any()]
    if gapped:
        raise ValidationError(f"gaps in daily bar series for: {gapped}")

    return close[symbols], volume[symbols]


def compute_ewma_vol(close: pd.DataFrame, cfg: Config, seed_window: int = 60):
    log_ret = np.log(close / close.shift(1)).dropna(how="all")
    lam = 0.5 ** (1 / cfg.ewma_halflife)
    sigmas = {}
    for ticker in close.columns:
        r = log_ret[ticker].dropna().values
        if len(r) < seed_window + 10:
            raise ValidationError(f"not enough return history for {ticker} to seed EWMA vol")
        var = np.var(r[:seed_window])
        for rt in r[seed_window:]:
            var = lam * var + (1 - lam) * rt ** 2
        sigma = float(np.sqrt(var * 252))
        sigmas[ticker] = float(np.clip(sigma, cfg.vol_floor, cfg.vol_cap))
    return pd.Series(sigmas, name="sigma")


def compute_covariance(close: pd.DataFrame, cfg: Config):
    log_ret = np.log(close / close.shift(1)).dropna()
    window_ret = log_ret.tail(cfg.cov_window)
    lw = LedoitWolf().fit(window_ret.values)
    Sigma = lw.covariance_ * 252
    return pd.DataFrame(Sigma, index=close.columns, columns=close.columns)


def compute_adv20(close: pd.DataFrame, volume: pd.DataFrame, n: int = 20):
    dollar_vol = (close * volume).tail(n)
    return dollar_vol.mean()


# ---------------------------------------------------------------------------
# S4: raw weights
# ---------------------------------------------------------------------------

def compute_raw_weights(df: pd.DataFrame, sigma: pd.Series, cfg: Config):
    d = df.set_index("ticker")
    raw = (21 - d["ranking"]) / sigma[d.index]

    if cfg.use_risk_index or cfg.use_sentiment_index:
        n = len(d)
        p_risk = d["risk_index"].rank(method="average") / n
        p_sent = d["sentiment_index"].rank(method="average") / n
        if cfg.use_risk_index:
            raw = raw * (1 - 0.20 * p_risk)
        if cfg.use_sentiment_index:
            raw = raw * (0.92 + 0.16 * p_sent)

    w = raw / raw.sum()
    return w, raw


# ---------------------------------------------------------------------------
# S5: constraints (cap, floor, liquidity) - renormalise each pass
# ---------------------------------------------------------------------------

def apply_constraints(w: pd.Series, adv20: pd.Series, V: float, cfg: Config):
    active = w.copy()
    dropped = []
    bound_log = {t: [] for t in w.index}

    for _ in range(cfg.max_constraint_passes):
        prev = active.copy()

        over_cap = active[active > cfg.position_cap].index.tolist()
        for t in over_cap:
            if "position_cap" not in bound_log[t]:
                bound_log[t].append("position_cap")
        active = active.clip(upper=cfg.position_cap)
        active = active / active.sum()

        liq_cap = cfg.liquidity_frac * adv20[active.index] / V
        over_liq = active[active > liq_cap].index.tolist()
        for t in over_liq:
            if "liquidity" not in bound_log[t]:
                bound_log[t].append("liquidity")
        active = pd.Series(np.minimum(active.values, liq_cap.values), index=active.index)
        active = active / active.sum()

        below_floor = active[active < cfg.position_floor].index.tolist()
        if below_floor:
            for t in below_floor:
                if t not in dropped:
                    dropped.append(t)
            active = active.drop(below_floor)
            if active.empty:
                raise ValidationError("all names were dropped by the floor constraint - check inputs")
            active = active / active.sum()

        if active.index.equals(prev.index) and np.allclose(
            active.values, prev.reindex(active.index).values, atol=1e-9
        ):
            break

    bound_log = {t: v for t, v in bound_log.items() if v}
    return active, dropped, bound_log


# ---------------------------------------------------------------------------
# S6: vol targeting + regime scaling
# ---------------------------------------------------------------------------

def portfolio_vol(w: pd.Series, Sigma: pd.DataFrame):
    idx = w.index
    S = Sigma.loc[idx, idx].values
    return float(np.sqrt(w.values @ S @ w.values))


def compute_regime_scalar(benchmark_close: pd.Series, supplied_regime, cfg: Config, state_path=REGIME_STATE_PATH):
    ma200 = benchmark_close.rolling(200).mean()
    above = (benchmark_close > ma200).astype("Int64")
    recent = above.tail(cfg.regime_confirm_days)
    if len(recent) < cfg.regime_confirm_days or recent.isna().any():
        raise ValidationError("not enough benchmark history to compute a confirmed 200-day MA regime")

    our_side = int(recent.iloc[-1]) if recent.nunique() == 1 else None

    state = {"confirmed_regime": 1, "flip_count": 0, "flip_log": []}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    confirmed = state.get("confirmed_regime", 1)
    if our_side is not None and our_side != confirmed:
        confirmed = our_side
        state["flip_count"] = state.get("flip_count", 0) + 1
        state.setdefault("flip_log", []).append(
            {"date": datetime.now(timezone.utc).isoformat(), "new_regime": confirmed}
        )

    state["confirmed_regime"] = confirmed
    state_path.write_text(json.dumps(state, indent=2))

    if supplied_regime is not None and int(supplied_regime) != confirmed:
        print(
            f"  note: supplied regime={int(supplied_regime)} disagrees with our confirmed "
            f"200-day-MA regime={confirmed} (needs {cfg.regime_confirm_days} consecutive days to flip); "
            f"using our confirmed value"
        )

    k_regime = 1.00 if confirmed == 1 else cfg.regime_off_scalar
    return k_regime, confirmed, state.get("flip_count", 0)


# ---------------------------------------------------------------------------
# S7: cross-check (log only)
# ---------------------------------------------------------------------------

def cross_check(df: pd.DataFrame, sigma: pd.Series):
    sigma_pct = sigma.rank(pct=True)
    vol_idx_pct = df.set_index("ticker")["volatility_index"].rank(pct=True)
    diffs = (sigma_pct - vol_idx_pct.reindex(sigma_pct.index)).abs()
    flagged = diffs[diffs > 0.4]
    for t, d in flagged.items():
        print(f"  cross-check WARNING: {t} computed-vol percentile vs supplied volatility_index percentile differ by {d:.2f}")
    return flagged.to_dict()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_pipeline(csv_path, output_path, cfg: Config, basis="equity"):
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        print("ERROR: set ALPACA_API_KEY and ALPACA_SECRET_KEY (in .env or env vars).")
        sys.exit(1)

    trading_client = TradingClient(api_key, secret_key, paper=True)
    data_client = StockHistoricalDataClient(api_key, secret_key)

    staleness_errors, file_hash, mtime = check_staleness(csv_path, cfg)

    df = load_weekly_csv(csv_path)
    errors = staleness_errors + validate(df, trading_client, cfg)
    if errors:
        print("VALIDATION FAILED - refusing to trade:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    tickers = df["ticker"].tolist()
    symbols = tickers + [cfg.benchmark] if cfg.apply_regime_scaling else tickers

    print(f"Fetching {cfg.lookback_days}+ days of daily bars for {len(symbols)} symbols...")
    bars = fetch_daily_bars(data_client, symbols, cfg.lookback_days)
    close, volume = bars_to_frames(bars, symbols, cfg.lookback_days)

    sigma = compute_ewma_vol(close[tickers], cfg)
    Sigma = compute_covariance(close[tickers], cfg)
    adv20 = compute_adv20(close[tickers], volume[tickers])

    w_raw, raw = compute_raw_weights(df, sigma, cfg)

    account = trading_client.get_account()
    V = float(getattr(account, basis))

    w_constrained, dropped, bound_log = apply_constraints(w_raw, adv20, V, cfg)

    sigma_p = portfolio_vol(w_constrained, Sigma)
    k_vol = min(1.0, cfg.sigma_target / sigma_p) if sigma_p > 0 else 1.0
    w_after_vol = w_constrained * k_vol

    if cfg.apply_regime_scaling:
        supplied_regime = df["regime"].iloc[0]
        k_regime, confirmed_regime, flip_count = compute_regime_scalar(close[cfg.benchmark], supplied_regime, cfg)
    else:
        # This feed's `regime` is a per-stock trend flag (close vs that stock's own 200-day
        # SMA), not a single market-wide flag - there's nothing here to derive one portfolio-wide
        # k_regime scalar from. Carried through to the output CSV as information only.
        k_regime, confirmed_regime, flip_count = 1.0, None, None

    w_final = w_after_vol * k_regime
    cash = 1 - w_final.sum()

    cross_check_flags = cross_check(df, sigma)

    out = df.set_index("ticker").copy()
    out["sigma"] = sigma.reindex(out.index)
    out["weight_raw"] = w_raw.reindex(out.index)
    out["weight_after_constraints"] = w_constrained.reindex(out.index)
    out["weight_after_vol_target"] = w_after_vol.reindex(out.index)
    out["position_size"] = w_final.reindex(out.index).fillna(0.0)
    out["dropped_by_floor"] = out.index.isin(dropped)
    out["bound_constraint"] = [",".join(bound_log.get(t, [])) for t in out.index]
    out = out.reset_index()
    out.to_csv(output_path, index=False)

    run_log = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "input_csv": str(csv_path),
        "input_csv_hash": file_hash,
        "input_csv_mtime": mtime.isoformat(),
        "basis": basis,
        "portfolio_value": V,
        "sigma": sigma.to_dict(),
        "weight_raw": w_raw.to_dict(),
        "weight_after_constraints": w_constrained.to_dict(),
        "weight_after_vol_target": w_after_vol.to_dict(),
        "weight_final": w_final.to_dict(),
        "sigma_p": sigma_p,
        "k_vol": k_vol,
        "k_regime": k_regime,
        "confirmed_regime": confirmed_regime,
        "regime_flip_count_lifetime": flip_count,
        "dropped_by_floor": dropped,
        "bound_constraints": bound_log,
        "cash_pct": cash,
        "cross_check_flags": cross_check_flags,
        "config": asdict(cfg),
    }
    log_path = LOG_DIR / f"run_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    log_path.write_text(json.dumps(run_log, indent=2, default=str))

    # Only mark this file as "processed" once the run completed successfully,
    # so a mid-pipeline failure (e.g. bad bars) can be safely retried on the same file.
    INPUT_HASH_STATE_PATH.write_text(
        json.dumps({"hash": file_hash, "path": str(csv_path), "run_at": run_log["run_at"]}, indent=2)
    )

    print()
    print(f"Wrote {output_path} ({len(out)} rows, {len(w_final)} held names)")
    print(f"Wrote run log {log_path}")
    print(
        f"sigma_p={sigma_p:.3f}  k_vol={k_vol:.3f}  k_regime={k_regime:.2f} "
        f"(confirmed_regime={confirmed_regime})  cash={cash:.1%}"
    )
    if dropped:
        print(f"Dropped by floor constraint: {dropped}")
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv_path", help="Weekly ranked-stock CSV from the selection system")
    parser.add_argument("--output", default="target_positions.csv", help="Output CSV path (default: target_positions.csv)")
    parser.add_argument("--basis", default="equity", choices=["equity", "cash", "buying_power", "portfolio_value"])
    parser.add_argument("--use-risk-index", action="store_true", help="Enable S4.1 risk_index modifier (default off)")
    parser.add_argument("--use-sentiment-index", action="store_true", help="Enable S4.1 sentiment_index modifier (default off)")
    parser.add_argument(
        "--apply-regime-scaling",
        action="store_true",
        help="Enable market-wide k_regime de-risking computed from the benchmark's own 200-day MA "
        "(default off - this feed's Regime column is per-stock, not market-wide)",
    )
    parser.add_argument("--sigma-target", type=float, default=0.15)
    parser.add_argument("--position-cap", type=float, default=0.12)
    parser.add_argument("--position-floor", type=float, default=0.015)
    parser.add_argument("--benchmark", default="SPY")
    args = parser.parse_args()

    cfg = Config(
        sigma_target=args.sigma_target,
        position_cap=args.position_cap,
        position_floor=args.position_floor,
        use_risk_index=args.use_risk_index,
        use_sentiment_index=args.use_sentiment_index,
        apply_regime_scaling=args.apply_regime_scaling,
        benchmark=args.benchmark,
    )
    run_pipeline(args.csv_path, args.output, cfg, basis=args.basis)


if __name__ == "__main__":
    main()
