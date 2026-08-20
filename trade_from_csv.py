"""
Rebalance an Alpaca paper trading account to match target position sizes
from a CSV — normally the target_positions.csv written by
position_sizing.py, but a plain manual CSV also works. Designed to be
re-run weekly with a new CSV: it buys positions under target, trims
positions over target, and closes positions no longer in the CSV at all.

CSV format (header required, column names are case-insensitive):
    ticker,position_size
    AAPL,0.095
    MSFT,0.0725
    ...

`position_size` is a fraction of account equity (0.095 = 9.5%). A plain
"percent" column (8 = 8%) is also accepted for manually-authored CSVs.

Order generation follows position-sizing-spec.md S8:
    - Trade only if abs(target - current) > max(MIN_TRADE_ABS, NO_TRADE_BAND * target)
    - Positions absent from the CSV are exited in full via close_position
      (NOTE: the spec's rank-25 exit hysteresis is NOT implemented — the
      weekly CSV only ever carries ranks 1-20, so there is no rank-21..25
      data to check a dropped name against)
    - All sells/closes are submitted first, polled until they confirm
      filled, then buys are submitted. If any sell/close doesn't confirm
      within the timeout, remaining buys are aborted and an alert is printed.
    - Orders below MIN_TRADE_ABS are skipped.

Setup:
    1. Get paper trading API keys from https://app.alpaca.markets/paper/dashboard/overview
    2. Put them in .env:
         ALPACA_API_KEY=xxxx
         ALPACA_SECRET_KEY=xxxx
    3. Run:
         python trade_from_csv.py target_positions.csv --dry-run
         python trade_from_csv.py target_positions.csv
"""

import argparse
import csv
import os
import sys
import time

from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus
from alpaca.common.exceptions import APIError

load_dotenv()

TICKER_ALIASES = {"ticker", "symbol", "stock"}
SIZE_ALIASES = {"position_size", "weight_final", "weight"}          # fraction, e.g. 0.095
PERCENT_ALIASES = {"percent", "percentage", "allocation", "pct"}     # percent, e.g. 9.5

NO_TRADE_BAND = 0.20      # fractional deviation from target before trading (spec S10)
MIN_TRADE_ABS = 25.0      # absolute $ floor on trade size (spec S10)
FILL_POLL_TIMEOUT = 60    # seconds to wait for a sell/close to confirm before aborting buys
FILL_POLL_INTERVAL = 2

TERMINAL_STATUSES = {
    OrderStatus.FILLED,
    OrderStatus.PARTIALLY_FILLED,
    OrderStatus.CANCELED,
    OrderStatus.EXPIRED,
    OrderStatus.REJECTED,
}
OK_STATUSES = {OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED}


def load_targets(csv_path):
    """Returns dict of {ticker: percent}, deduping tickers by summing their percents.
    Accepts either a `position_size` fraction column or a `percent` column."""
    targets = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("CSV appears to be empty (no header row).")

        norm = {name: name.strip().lower() for name in reader.fieldnames}
        ticker_col = next((n for n, low in norm.items() if low in TICKER_ALIASES), None)
        size_col = next((n for n, low in norm.items() if low in SIZE_ALIASES), None)
        percent_col = next((n for n, low in norm.items() if low in PERCENT_ALIASES), None)

        if ticker_col is None or (size_col is None and percent_col is None):
            raise ValueError(
                f"Could not find ticker + position_size/percent columns in header {reader.fieldnames}. "
                f"Expected one of {TICKER_ALIASES} and one of {SIZE_ALIASES | PERCENT_ALIASES}."
            )
        is_fraction = size_col is not None
        value_col = size_col or percent_col

        for i, row in enumerate(reader, start=2):
            raw_ticker = (row.get(ticker_col) or "").strip().upper()
            raw_value = (row.get(value_col) or "").strip()
            if not raw_ticker:
                continue
            try:
                value = float(raw_value)
            except ValueError:
                raise ValueError(f"Row {i}: could not parse value '{raw_value}' for {raw_ticker}")
            percent = value * 100 if is_fraction else value
            if percent <= 0:
                continue
            if raw_ticker in targets:
                print(f"  note: {raw_ticker} appears more than once, summing")
                targets[raw_ticker] += percent
            else:
                targets[raw_ticker] = percent
    return targets


def get_basis_value(account, basis):
    return float(getattr(account, basis))


def get_current_values(client):
    """Returns dict of {symbol: current market value (float)} for open positions."""
    return {p.symbol: float(p.market_value) for p in client.get_all_positions()}


def plan_actions(targets, current_values, basis_value, no_trade_band=NO_TRADE_BAND, min_trade_abs=MIN_TRADE_ABS):
    """
    Compares target % allocations against current $ position values and
    returns a list of planned actions: (symbol, kind, current_value, target_value, amount)
    kind is one of "buy", "sell", "close".
    """
    actions = []
    all_symbols = set(targets) | set(current_values)

    for symbol in sorted(all_symbols):
        percent = targets.get(symbol)
        current_value = current_values.get(symbol, 0.0)

        if percent is None:
            # Held but no longer in the target list -> close entirely (spec S8).
            if current_value > 0:
                actions.append((symbol, "close", current_value, 0.0, current_value))
            continue

        target_value = basis_value * percent / 100
        diff = target_value - current_value

        no_trade_threshold = max(min_trade_abs, no_trade_band * target_value)
        if abs(diff) <= no_trade_threshold:
            continue  # within the no-trade band

        if diff > 0:
            actions.append((symbol, "buy", current_value, target_value, round(diff, 2)))
        else:
            actions.append((symbol, "sell", current_value, target_value, round(-diff, 2)))

    return actions


def print_plan(actions, basis_value):
    print(f"{'SYMBOL':<8} {'ACTION':<6}{'CURRENT':>12}{'TARGET':>12}{'ORDER $':>12}")
    for symbol, kind, current_value, target_value, amount in actions:
        print(f"{symbol:<8} {kind.upper():<6}{current_value:>12,.2f}{target_value:>12,.2f}{amount:>12,.2f}")
    if not actions:
        print("(nothing to do — positions already match targets)")


def wait_for_fill(client, order_id, timeout=FILL_POLL_TIMEOUT, poll_interval=FILL_POLL_INTERVAL):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get_order_by_id(order_id)
        if last.status in TERMINAL_STATUSES:
            return last
        time.sleep(poll_interval)
    return last  # may be non-terminal (timeout)


def execute_actions(client, actions, min_trade_abs=MIN_TRADE_ABS, poll_timeout=FILL_POLL_TIMEOUT):
    """Sells/closes first, polled to confirm fill, then buys (spec S8 - mandatory order)."""
    sells = [a for a in actions if a[1] in ("close", "sell")]
    buys = [a for a in actions if a[1] == "buy"]

    sell_order_ids = []
    for symbol, kind, current_value, target_value, amount in sells:
        try:
            if kind == "close":
                # Full exits use close_position, not a computed notional, to avoid fractional dust.
                o = client.close_position(symbol)
                print(f"  {symbol:<6} CLOSE  full position (${amount:,.2f}) submitted, order id {o.id}")
                sell_order_ids.append(o.id)
            else:
                if amount < min_trade_abs:
                    print(f"  {symbol:<6} SKIP   sell ${amount:,.2f} below ${min_trade_abs:.0f} minimum")
                    continue
                o = client.submit_order(
                    MarketOrderRequest(
                        symbol=symbol, notional=amount, side=OrderSide.SELL, time_in_force=TimeInForce.DAY
                    )
                )
                print(f"  {symbol:<6} SELL   ${amount:>10,.2f}  order id {o.id} [{o.status}]")
                sell_order_ids.append(o.id)
        except APIError as e:
            print(f"  {symbol:<6} {kind.upper():<6} ${amount:>10,.2f}  FAILED: {e}")

    if sell_order_ids:
        print("\nWaiting for sell/close orders to confirm before submitting buys...")
        unfilled = []
        for oid in sell_order_ids:
            o = wait_for_fill(client, oid, timeout=poll_timeout)
            status = o.status if o is not None else "unknown"
            if o is None or status not in OK_STATUSES:
                unfilled.append((oid, status))
        if unfilled:
            print(f"ALERT: {len(unfilled)} sell/close order(s) did not confirm filled: {unfilled}")
            print("Aborting remaining buy orders — re-run once the account settles.")
            return

    for symbol, kind, current_value, target_value, amount in buys:
        if amount < min_trade_abs:
            print(f"  {symbol:<6} SKIP   buy ${amount:,.2f} below ${min_trade_abs:.0f} minimum")
            continue
        try:
            o = client.submit_order(
                MarketOrderRequest(symbol=symbol, notional=amount, side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
            )
            print(f"  {symbol:<6} BUY    ${amount:>10,.2f}  order id {o.id} [{o.status}]")
        except APIError as e:
            print(f"  {symbol:<6} BUY    ${amount:>10,.2f}  FAILED: {e}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv_path", help="Path to CSV file with ticker + position_size (or percent) columns")
    parser.add_argument(
        "--basis",
        choices=["equity", "cash", "buying_power", "portfolio_value"],
        default="equity",
        help="Account value used as the 100%% base for sizing (default: equity)",
    )
    parser.add_argument("--no-trade-band", type=float, default=NO_TRADE_BAND, help="Fractional deviation before trading (default: 0.20)")
    parser.add_argument("--min-trade-abs", type=float, default=MIN_TRADE_ABS, help="Absolute $ floor on trade size (default: 25)")
    parser.add_argument("--poll-timeout", type=int, default=FILL_POLL_TIMEOUT, help="Seconds to wait for sell/close fills before aborting buys")
    parser.add_argument("--dry-run", action="store_true", help="Compute and print planned trades without submitting them")
    args = parser.parse_args()

    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        print("ERROR: set ALPACA_API_KEY and ALPACA_SECRET_KEY (in .env or env vars) — paper trading keys.")
        print("Get them at https://app.alpaca.markets/paper/dashboard/overview")
        sys.exit(1)

    try:
        targets = load_targets(args.csv_path)
    except (ValueError, OSError) as e:
        print(f"ERROR reading CSV: {e}")
        sys.exit(1)

    if not targets:
        print("No valid target positions found in CSV.")
        sys.exit(1)

    total_percent = sum(targets.values())
    print(f"Loaded {len(targets)} target positions totaling {total_percent:.2f}% of {args.basis}.")
    if total_percent > 100:
        print(f"WARNING: total percent ({total_percent:.2f}%) exceeds 100% — buying power may not cover all orders.")

    client = TradingClient(api_key, secret_key, paper=True)

    account = client.get_account()
    basis_value = get_basis_value(account, args.basis)
    current_values = get_current_values(client)

    print(f"Account {args.basis}: ${basis_value:,.2f}")
    print(f"Account buying power: ${float(account.buying_power):,.2f}")
    print(f"Currently holding {len(current_values)} position(s).")
    print()

    actions = plan_actions(targets, current_values, basis_value, args.no_trade_band, args.min_trade_abs)
    print_plan(actions, basis_value)
    print()

    if args.dry_run:
        print(f"Dry run: {len(actions)} action(s) planned, nothing submitted.")
        return

    if not actions:
        print("Nothing to do.")
        return

    execute_actions(client, actions, min_trade_abs=args.min_trade_abs, poll_timeout=args.poll_timeout)
    print()
    print(f"Done: {len(actions)} action(s) processed.")


if __name__ == "__main__":
    main()
