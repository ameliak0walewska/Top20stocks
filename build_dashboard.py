#!/usr/bin/env python3
"""
Builds dashboard.html from the latest position_sizing.py run log + today's
target_positions.csv, and appends a summary row to logs/dashboard_history.jsonl
so the dashboard can chart cash%/regime exposure over time.

No third-party dependencies - stdlib only, so it can never break on an
environment mismatch the way the main pipeline's pandas/numpy stack can.

Run after position_sizing.py + trade_from_csv.py, from the project directory:
    python3 build_dashboard.py
"""
import csv
import glob
import html
import json
import os
from datetime import datetime, timezone

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(PROJECT_DIR, "logs")
HISTORY_PATH = os.path.join(LOG_DIR, "dashboard_history.jsonl")
DASHBOARD_PATH = os.path.join(PROJECT_DIR, "dashboard.html")
DOCS_DIR = os.path.join(PROJECT_DIR, "docs")
DOCS_DASHBOARD_PATH = os.path.join(DOCS_DIR, "index.html")
TARGET_POSITIONS_PATH = os.path.join(PROJECT_DIR, "target_positions.csv")
TOP20_PATH = os.path.join(PROJECT_DIR, "top20.csv")


def find_latest_run_log():
    candidates = sorted(glob.glob(os.path.join(LOG_DIR, "run_*.json")))
    if not candidates:
        raise SystemExit("No logs/run_*.json found - run position_sizing.py first.")
    return candidates[-1]


def load_sectors():
    sectors = {}
    if not os.path.exists(TOP20_PATH):
        return sectors
    with open(TOP20_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            norm = {k.strip().lower(): v for k, v in row.items()}
            ticker = (norm.get("ticker") or "").strip().upper()
            if ticker:
                sectors[ticker] = norm.get("sector", "") or ""
    return sectors


def load_target_weights():
    rows = []
    if not os.path.exists(TARGET_POSITIONS_PATH):
        return rows
    with open(TARGET_POSITIONS_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            norm = {k.strip().lower(): v for k, v in row.items()}
            ticker = (norm.get("ticker") or "").strip().upper()
            try:
                weight = float(norm.get("position_size", 0) or 0)
            except ValueError:
                weight = 0.0
            if ticker and weight > 0:
                rows.append({"ticker": ticker, "weight": weight})
    rows.sort(key=lambda r: -r["weight"])
    return rows


def append_history(record):
    os.makedirs(LOG_DIR, exist_ok=True)
    existing = []
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    existing.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    # Replace any existing record for the same date (re-runs on the same day
    # via --force shouldn't pile up duplicate history points).
    existing = [r for r in existing if r.get("date") != record["date"]]
    existing.append(record)
    existing.sort(key=lambda r: r["date"])
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        for r in existing:
            f.write(json.dumps(r) + "\n")
    return existing


def svg_line_chart(history, key, chart_id, y_fmt, y_is_pct=True):
    """Small single-series line chart (one axis, no dual-axis). Returns an
    inline SVG string plus the data attributes a tiny shared hover script uses."""
    width, height = 520, 160
    pad_l, pad_r, pad_t, pad_b = 40, 16, 16, 28

    points = [(r["date"], r.get(key)) for r in history if r.get(key) is not None]
    if len(points) < 2:
        return (
            f'<div class="chart-empty">Not enough history yet '
            f'({len(points)} day{"s" if len(points) != 1 else ""} logged) '
            f'- check back after a few more nightly runs.</div>'
        )

    values = [v for _, v in points]
    vmin, vmax = min(values), max(values)
    if vmin == vmax:
        vmin -= 0.05
        vmax += 0.05
    span = vmax - vmin

    def x_for(i):
        if len(points) == 1:
            return pad_l
        return pad_l + (width - pad_l - pad_r) * i / (len(points) - 1)

    def y_for(v):
        return pad_t + (height - pad_t - pad_b) * (1 - (v - vmin) / span)

    coords = [(x_for(i), y_for(v)) for i, (_, v) in enumerate(points)]
    path_d = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in coords)

    gridlines = ""
    for frac in (0, 0.5, 1):
        gy = pad_t + (height - pad_t - pad_b) * (1 - frac)
        label = y_fmt(vmin + span * frac)
        gridlines += (
            f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}" '
            f'class="gridline" />'
            f'<text x="{pad_l - 6}" y="{gy + 3:.1f}" class="axis-label" text-anchor="end">{label}</text>'
        )

    dots = ""
    for i, ((d, v), (x, y)) in enumerate(zip(points, coords)):
        dots += (
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" class="dot" '
            f'data-date="{html.escape(d)}" data-value="{html.escape(y_fmt(v))}" '
            f'data-chart="{chart_id}" />'
        )

    last_label = y_fmt(values[-1])
    return f'''
<svg viewBox="0 0 {width} {height}" class="line-chart" id="{chart_id}">
  {gridlines}
  <path d="{path_d}" class="series-line" fill="none" />
  {dots}
  <text x="{width - pad_r}" y="{pad_t - 2}" class="axis-label" text-anchor="end">latest: {last_label}</text>
</svg>
<div class="tooltip" id="{chart_id}-tooltip"></div>
'''


def pct_fmt(v):
    return f"{v * 100:.1f}%"


def num_fmt(v):
    return f"{v:.2f}"


def build_html(run_log, history, weights, sectors):
    regime = run_log.get("regime", {})
    cash_pct = run_log.get("cash_pct", 0.0)
    k_regime = run_log.get("applied_k_regime", regime.get("k_regime", 0.0))
    sigma_p = run_log.get("sigma_p", 0.0)
    dropped = run_log.get("dropped_by_floor", [])
    run_at = run_log.get("run_at", "")
    try:
        run_dt = datetime.fromisoformat(run_at.replace("Z", "+00:00"))
        run_at_display = run_dt.strftime("%A %d %B %Y, %H:%M UTC")
    except Exception:
        run_at_display = run_at

    n_held = len(weights)
    distance = regime.get("distance")
    benchmark = regime.get("benchmark_used", "SPY")
    distance_str = f"{distance * 100:+.2f}%" if distance is not None else "n/a"
    cold_start = regime.get("cold_start", False)

    var_result = run_log.get("var_1d")
    if var_result:
        binding = var_result.get("binding", False)
        budget = var_result.get("var_budget_pct")
        sub = (
            f'budget {budget:.1%} — positions scaled down (k_var={var_result.get("k_var", 1.0):.2f})'
            if binding
            else (f'within {budget:.1%} budget, no scaling' if budget is not None else '')
        )
        var_tile = (
            f'<div class="stat-tile"><div class="stat-label">1-day VaR ({var_result["confidence"]:.0%}, QuantLib)</div>'
            f'<div class="stat-value">${var_result["var_usd"]:,.0f}</div>'
            + (f'<div class="stat-sub">{html.escape(sub)}</div>' if sub else '')
            + '</div>'
        )
    else:
        var_tile = (
            '<div class="stat-tile"><div class="stat-label">1-day VaR (95%, QuantLib)</div>'
            '<div class="stat-value muted-value">n/a</div></div>'
        )

    rows_html = ""
    for w in weights:
        ticker = html.escape(w["ticker"])
        sector = html.escape(sectors.get(w["ticker"], "—"))
        pct = w["weight"] * 100
        rows_html += (
            f'<tr><td class="tkr">{ticker}</td><td class="sector">{sector}</td>'
            f'<td class="num">{pct:.2f}%</td>'
            f'<td class="bar-cell"><div class="bar" style="width:{min(pct * 6, 100):.1f}%"></div></td></tr>\n'
        )

    dropped_html = ""
    if dropped:
        dropped_list = ", ".join(html.escape(t) for t in dropped)
        dropped_html = (
            f'<p class="muted">Dropped by the position-floor constraint this run '
            f'(sized too small / too risky to hold at a meaningful weight): {dropped_list}</p>'
        )

    cash_chart = svg_line_chart(history, "cash_pct", "cash-chart", pct_fmt)
    regime_chart = svg_line_chart(history, "k_regime", "regime-chart", num_fmt)

    cold_start_note = (
        '<p class="muted">This is the first logged run, so the regime scalar was applied '
        "immediately without the usual 3-day confirmation smoothing.</p>"
        if cold_start
        else ""
    )

    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Top 20 Stocks — Daily Dashboard</title>
<style>
  :root {{
    color-scheme: light;
    --surface-1:      #fcfcfb;
    --page:           #f9f9f7;
    --text-primary:   #0b0b0b;
    --text-secondary: #52514e;
    --text-muted:     #898781;
    --gridline:       #e1e0d9;
    --baseline:       #c3c2b7;
    --border:         rgba(11,11,11,0.10);
    --series-1:       #2a78d6;
    --series-2:       #eb6834;
    --tile-bg:        #ffffff;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      color-scheme: dark;
      --surface-1:      #1a1a19;
      --page:           #0d0d0d;
      --text-primary:   #ffffff;
      --text-secondary: #c3c2b7;
      --text-muted:     #898781;
      --gridline:       #2c2c2a;
      --baseline:       #383835;
      --border:         rgba(255,255,255,0.10);
      --series-1:       #3987e5;
      --series-2:       #d95926;
      --tile-bg:        #1f1f1e;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--page);
    color: var(--text-primary);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    padding: 32px 20px 60px;
  }}
  .wrap {{ max-width: 880px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .subtitle {{ color: var(--text-secondary); font-size: 14px; margin: 0 0 28px; }}
  .disclaimer {{
    font-size: 12px; color: var(--text-muted); border: 1px solid var(--border);
    border-radius: 8px; padding: 10px 14px; margin: 0 0 28px; background: var(--surface-1);
  }}
  .stat-row {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 28px;
  }}
  .stat-tile {{
    background: var(--tile-bg); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px 16px;
  }}
  .stat-label {{ font-size: 12px; color: var(--text-muted); margin-bottom: 6px; }}
  .stat-value {{ font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums; }}
  .stat-value.muted-value {{ color: var(--text-muted); font-weight: 500; }}
  .stat-sub {{ font-size: 11px; color: var(--text-muted); margin-top: 4px; }}
  .card {{
    background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px;
    padding: 18px 20px; margin-bottom: 24px;
  }}
  .card h2 {{ font-size: 15px; margin: 0 0 4px; }}
  .card .card-sub {{ font-size: 12px; color: var(--text-muted); margin: 0 0 12px; }}
  .chart-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
  .line-chart {{ width: 100%; height: auto; overflow: visible; }}
  .gridline {{ stroke: var(--gridline); stroke-width: 1; }}
  .axis-label {{ fill: var(--text-muted); font-size: 10px; }}
  .series-line {{ stroke: var(--series-1); stroke-width: 2; stroke-linecap: round; }}
  #regime-chart .series-line {{ stroke: var(--series-2); }}
  .dot {{ fill: var(--series-1); cursor: pointer; }}
  #regime-chart .dot {{ fill: var(--series-2); }}
  .tooltip {{
    position: absolute; pointer-events: none; opacity: 0; transition: opacity .1s;
    background: var(--text-primary); color: var(--surface-1); font-size: 11px;
    padding: 4px 8px; border-radius: 6px; white-space: nowrap; transform: translate(-50%, -130%);
  }}
  .chart-wrap {{ position: relative; }}
  .chart-empty {{ font-size: 12px; color: var(--text-muted); padding: 40px 0; text-align: center; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; font-size: 11px; color: var(--text-muted); font-weight: 500;
        border-bottom: 1px solid var(--border); padding: 6px 8px; }}
  td {{ padding: 6px 8px; border-bottom: 1px solid var(--gridline); }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  td.tkr {{ font-weight: 600; }}
  td.sector {{ color: var(--text-secondary); }}
  .bar-cell {{ width: 120px; }}
  .bar {{ height: 6px; background: var(--series-1); border-radius: 3px; }}
  .muted {{ font-size: 12px; color: var(--text-muted); }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Top 20 Stocks — Daily Dashboard</h1>
  <p class="subtitle">Last run: {html.escape(run_at_display)}</p>
  <p class="disclaimer">This is a personal research/backtesting tool, not investment advice.
    Numbers come from a dry-run pipeline — nothing here has been submitted as a real order.</p>

  <div class="stat-row">
    <div class="stat-tile"><div class="stat-label">Cash held</div><div class="stat-value">{pct_fmt(cash_pct)}</div></div>
    <div class="stat-tile"><div class="stat-label">Regime scalar (k)</div><div class="stat-value">{num_fmt(k_regime)}</div></div>
    <div class="stat-tile"><div class="stat-label">Target volatility</div><div class="stat-value">{pct_fmt(sigma_p)}</div></div>
    <div class="stat-tile"><div class="stat-label">Positions held</div><div class="stat-value">{n_held}/20</div></div>
    {var_tile}
  </div>

  <div class="card">
    <h2>Market regime</h2>
    <p class="card-sub">{html.escape(benchmark)} is {distance_str} vs its own 200-day moving average.</p>
    {cold_start_note}
    <div class="chart-grid">
      <div>
        <div class="card-sub">Cash % held over time</div>
        <div class="chart-wrap">{cash_chart}</div>
      </div>
      <div>
        <div class="card-sub">Regime scalar over time</div>
        <div class="chart-wrap">{regime_chart}</div>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>Today's target portfolio</h2>
    <p class="card-sub">{n_held} positions, sorted by weight</p>
    <table>
      <thead><tr><th>Ticker</th><th>Sector</th><th>Weight</th><th></th></tr></thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>
    {dropped_html}
  </div>
</div>
<script>
  document.querySelectorAll('.dot').forEach(function (dot) {{
    dot.addEventListener('mouseenter', function () {{
      var chart = dot.getAttribute('data-chart');
      var tip = document.getElementById(chart + '-tooltip');
      if (!tip) return;
      tip.textContent = dot.getAttribute('data-date') + ': ' + dot.getAttribute('data-value');
      var rect = dot.getBoundingClientRect();
      var parentRect = dot.closest('.chart-wrap').getBoundingClientRect();
      tip.style.left = (rect.left - parentRect.left + rect.width / 2) + 'px';
      tip.style.top = (rect.top - parentRect.top) + 'px';
      tip.style.opacity = '1';
    }});
    dot.addEventListener('mouseleave', function () {{
      var chart = dot.getAttribute('data-chart');
      var tip = document.getElementById(chart + '-tooltip');
      if (tip) tip.style.opacity = '0';
    }});
  }});
</script>
</body>
</html>
'''


def main():
    run_log_path = find_latest_run_log()
    with open(run_log_path, encoding="utf-8") as f:
        run_log = json.load(f)

    run_at = run_log.get("run_at", datetime.now(timezone.utc).isoformat())
    date_str = run_at[:10]

    record = {
        "date": date_str,
        "run_at": run_at,
        "cash_pct": run_log.get("cash_pct", 0.0),
        "k_regime": run_log.get("applied_k_regime", run_log.get("regime", {}).get("k_regime")),
        "sigma_p": run_log.get("sigma_p", 0.0),
        "n_held": sum(1 for v in run_log.get("weight_final", {}).values() if v and v > 0),
        "dropped_by_floor": run_log.get("dropped_by_floor", []),
    }
    history = append_history(record)

    weights = load_target_weights()
    sectors = load_sectors()

    out_html = build_html(run_log, history, weights, sectors)

    with open(DASHBOARD_PATH, "w", encoding="utf-8") as f:
        f.write(out_html)

    # Also write into docs/ so it can be published as the site's index page
    # via GitHub Pages (Settings -> Pages -> Deploy from branch -> /docs).
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(DOCS_DASHBOARD_PATH, "w", encoding="utf-8") as f:
        f.write(out_html)
    # .nojekyll stops GitHub Pages running the file through Jekyll, which
    # would otherwise ignore/mangle anything starting with an underscore.
    nojekyll_path = os.path.join(DOCS_DIR, ".nojekyll")
    if not os.path.exists(nojekyll_path):
        open(nojekyll_path, "w").close()

    print(f"Dashboard written to {DASHBOARD_PATH} and {DOCS_DASHBOARD_PATH} ({len(history)} day(s) of history)")


if __name__ == "__main__":
    main()
