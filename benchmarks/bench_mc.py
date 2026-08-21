"""Throughput harness for the Monte Carlo risk engine - quantlib-risk-engine-spec.md S10.

Reports wall time, paths/second, peak memory, the CVaR value produced (to
assert correctness is preserved across changes), and a phase breakdown
(covariance estimation, Cholesky, path generation, measure computation).
Each configuration is run >=5 times; report median and IQR - single timings
are noise.

Usage:
    python benchmarks/bench_mc.py                    # run + compare to baseline fixture
    python benchmarks/bench_mc.py --record-baseline   # (re)write the baseline fixture
"""

import argparse
import json
import sys
import time
import tracemalloc
from pathlib import Path
from statistics import median

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from risk.covariance import correlated_sqrt, estimate_covariance
from risk.measures import conditional_value_at_risk
from risk.montecarlo import simulate_portfolio

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "baseline.json"
N_ASSETS = 20
N_OBS = 250
N_RUNS = 5
CVAR_RELATIVE_TOLERANCE = 1e-6   # deterministic changes must reproduce baseline CVaR exactly
CVAR_MC_TOLERANCE = 0.05         # sampling-affecting changes are checked against MC standard error instead


def _fixed_inputs(seed=123):
    rng = np.random.default_rng(seed)
    returns_matrix = rng.standard_normal((N_OBS, N_ASSETS)) * 0.015
    weights = np.full(N_ASSETS, 1.0 / N_ASSETS)
    return returns_matrix, weights


def _run_once(returns_matrix, weights, n_paths, generator, seed):
    phases = {}

    t0 = time.perf_counter()
    Sigma_daily, Sigma_ann = estimate_covariance(returns_matrix)
    phases["covariance_estimation_ms"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    correlated_sqrt(Sigma_daily)
    phases["cholesky_ms"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    path_result = simulate_portfolio(weights, Sigma_ann, horizon_days=5, n_paths=n_paths, seed=seed, generator=generator)
    phases["path_generation_ms"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    cvar = conditional_value_at_risk(path_result.terminal_returns, alpha=0.95)
    phases["measure_computation_ms"] = (time.perf_counter() - t0) * 1000

    total_ms = sum(phases.values())
    return total_ms, phases, cvar, path_result.n_paths


def benchmark(n_paths=50_000, generator="sobol", n_runs=N_RUNS):
    returns_matrix, weights = _fixed_inputs()

    totals, cvars, peak_mems = [], [], []
    phase_totals = {}

    for i in range(n_runs):
        tracemalloc.start()
        total_ms, phases, cvar, n_paths_actual = _run_once(returns_matrix, weights, n_paths, generator, seed=42)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        totals.append(total_ms)
        cvars.append(cvar)
        peak_mems.append(peak)
        for k, v in phases.items():
            phase_totals.setdefault(k, []).append(v)

    totals_sorted = sorted(totals)
    iqr = totals_sorted[int(0.75 * n_runs)] - totals_sorted[int(0.25 * n_runs)] if n_runs >= 4 else 0.0

    return {
        "n_paths": n_paths_actual,
        "generator": generator,
        "n_runs": n_runs,
        "wall_time_ms_median": median(totals),
        "wall_time_ms_iqr": iqr,
        "paths_per_second_median": n_paths_actual / (median(totals) / 1000),
        "peak_memory_bytes_median": median(peak_mems),
        "cvar": median(cvars),  # same seed every run -> deterministic, median just guards against a fluke
        "phase_breakdown_ms_median": {k: median(v) for k, v in phase_totals.items()},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n-paths", type=int, default=50_000)
    parser.add_argument("--generator", choices=["sobol", "mersenne"], default="sobol")
    parser.add_argument("--n-runs", type=int, default=N_RUNS)
    parser.add_argument("--record-baseline", action="store_true", help="(Re)write the baseline fixture instead of comparing to it")
    args = parser.parse_args()

    result = benchmark(args.n_paths, args.generator, args.n_runs)

    print(f"n_paths={result['n_paths']}  generator={result['generator']}  n_runs={result['n_runs']}")
    print(f"wall time: {result['wall_time_ms_median']:.1f}ms median (IQR {result['wall_time_ms_iqr']:.1f}ms)")
    print(f"throughput: {result['paths_per_second_median']:,.0f} paths/sec")
    print(f"peak memory: {result['peak_memory_bytes_median'] / 1e6:.1f} MB")
    print(f"CVaR: {result['cvar']:.6f}")
    print("phase breakdown (ms, median):")
    for k, v in result["phase_breakdown_ms_median"].items():
        print(f"  {k}: {v:.1f}")

    if args.record_baseline:
        FIXTURE_PATH.parent.mkdir(exist_ok=True, parents=True)
        FIXTURE_PATH.write_text(json.dumps(result, indent=2))
        print(f"\nBaseline recorded to {FIXTURE_PATH}")
        return

    if not FIXTURE_PATH.exists():
        print(f"\nNo baseline fixture at {FIXTURE_PATH} - run with --record-baseline first.")
        sys.exit(1)

    baseline = json.loads(FIXTURE_PATH.read_text())
    if baseline["generator"] != result["generator"] or baseline["n_paths"] != result["n_paths"]:
        print(
            f"\nBaseline was recorded with n_paths={baseline['n_paths']} generator={baseline['generator']} - "
            f"re-run with matching flags to compare, or --record-baseline to replace it."
        )
        sys.exit(1)

    rel_diff = abs(result["cvar"] - baseline["cvar"]) / abs(baseline["cvar"])
    speedup = baseline["wall_time_ms_median"] / result["wall_time_ms_median"]
    print(f"\nvs baseline: CVaR rel diff = {rel_diff:.2e}  wall time speedup = {speedup:.2f}x")

    if rel_diff > CVAR_MC_TOLERANCE:
        print(
            f"FAIL: CVaR changed by {rel_diff:.2%}, exceeding the {CVAR_MC_TOLERANCE:.0%} MC-sampling tolerance. "
            f"A speedup that changes the answer is not a speedup."
        )
        sys.exit(1)
    print("PASS: CVaR reproduced within tolerance.")


if __name__ == "__main__":
    main()
