"""Orchestration - the only module position_sizing.py imports (S3, S7).
Computes k_risk (CVaR-targeted) or falls back to the analytic k_vol
(volatility-targeted), and always logs both for comparison (S12).

RiskConfig.risk_engine defaults to "analytic", not "montecarlo" as S11's
config table lists. Build order S13 step 8 says switch the default only
after both the correctness tests (S9, all passing - see risk/tests/) AND
the CVAR_TARGET calibration against historical weights (S7) are done. The
calibration step needs months of live paper-trading weights this session
doesn't have, so the conservative default stays until that's done - flip it
with --risk-engine montecarlo once calibrated.
"""

import time
from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np

from .covariance import estimate_covariance
from .montecarlo import simulate_portfolio
from .measures import (
    conditional_value_at_risk,
    value_at_risk,
    annualize,
    portfolio_vol_from_paths,
    drawdown_stats,
)


@dataclass
class RiskConfig:
    risk_engine: str = "analytic"      # "analytic" or "montecarlo" - see module docstring on the default
    sigma_target: float = 0.15         # used only in analytic mode - the pre-existing vol target
    cvar_target: float = 0.25          # used only in montecarlo mode
    cvar_alpha: float = 0.95
    mc_n_paths: int = 50_000
    mc_horizon_days: int = 5
    mc_seed: int = 42
    mc_antithetic: bool = True
    mc_generator: str = "sobol"        # "sobol" or "mersenne"
    mc_drift: float = 0.0              # non-zero means CVaR is no longer a pure risk number - see S5.2
    risk_dry_run: bool = False         # compute and log without applying to weights


@dataclass
class RiskResult:
    k_risk: float
    risk_engine_used: str
    cvar_annualised: Optional[float]
    var_95_annualised: Optional[float]
    sigma_p_analytic: float
    sigma_p_simulated: Optional[float]
    sigma_p_diff_pct: Optional[float]
    n_paths: Optional[int]
    seed: Optional[int]
    generator: Optional[str]
    elapsed_ms: float
    salvaged: Optional[bool]
    salvage_delta: Optional[float]
    drawdown: Optional[dict]
    applied: bool  # False when risk_dry_run - k_risk was computed but not multiplied into weights


def compute_risk_scalar(weights: "pd.Series", Sigma_ann: np.ndarray, cfg: RiskConfig) -> RiskResult:
    """weights: pd.Series indexed by ticker, in the same order Sigma_ann's
    rows/columns correspond to. Returns a RiskResult; caller multiplies
    w_constrained by result.k_risk (unless risk_dry_run)."""
    w = weights.values
    t0 = time.perf_counter()

    sigma_p_analytic = float(np.sqrt(w @ Sigma_ann @ w))

    if cfg.risk_engine == "analytic":
        k_vol = min(1.0, cfg.sigma_target / sigma_p_analytic) if sigma_p_analytic > 0 else 1.0
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return RiskResult(
            k_risk=k_vol,
            risk_engine_used="analytic",
            cvar_annualised=None,
            var_95_annualised=None,
            sigma_p_analytic=sigma_p_analytic,
            sigma_p_simulated=None,
            sigma_p_diff_pct=None,
            n_paths=None,
            seed=None,
            generator=None,
            elapsed_ms=elapsed_ms,
            salvaged=None,
            salvage_delta=None,
            drawdown=None,
            applied=not cfg.risk_dry_run,
        )

    mu = np.full(len(w), cfg.mc_drift) if cfg.mc_drift else None
    path_result = simulate_portfolio(
        w, Sigma_ann,
        horizon_days=cfg.mc_horizon_days,
        n_paths=cfg.mc_n_paths,
        seed=cfg.mc_seed,
        generator=cfg.mc_generator,
        antithetic=cfg.mc_antithetic,
        mu=mu,
    )

    cvar_h = conditional_value_at_risk(path_result.terminal_returns, alpha=cfg.cvar_alpha)
    var_h = value_at_risk(path_result.terminal_returns, alpha=cfg.cvar_alpha)
    cvar_ann = annualize(cvar_h, cfg.mc_horizon_days)
    var_ann = annualize(var_h, cfg.mc_horizon_days)
    sigma_p_sim = portfolio_vol_from_paths(path_result.terminal_returns, cfg.mc_horizon_days)
    dd = drawdown_stats(path_result.paths)

    k_risk = min(1.0, cfg.cvar_target / cvar_ann) if cvar_ann > 0 else 1.0
    elapsed_ms = (time.perf_counter() - t0) * 1000

    diff_pct = (sigma_p_sim - sigma_p_analytic) / sigma_p_analytic if sigma_p_analytic > 0 else None
    if diff_pct is not None and abs(diff_pct) > 0.05:
        print(
            f"  WARNING: simulated sigma_p ({sigma_p_sim:.4f}) diverges from analytic "
            f"({sigma_p_analytic:.4f}) by {diff_pct:+.1%} - this should agree closely; "
            f"treat as a bug, not a finding (spec S6)"
        )
    if path_result.salvaged:
        print(f"  note: covariance matrix required spectral salvaging (max delta {path_result.salvage_delta:.4f})")

    return RiskResult(
        k_risk=k_risk,
        risk_engine_used="montecarlo",
        cvar_annualised=cvar_ann,
        var_95_annualised=var_ann,
        sigma_p_analytic=sigma_p_analytic,
        sigma_p_simulated=sigma_p_sim,
        sigma_p_diff_pct=diff_pct,
        n_paths=path_result.n_paths,
        seed=path_result.seed,
        generator=path_result.generator,
        elapsed_ms=elapsed_ms,
        salvaged=path_result.salvaged,
        salvage_delta=path_result.salvage_delta,
        drawdown=dd,
        applied=not cfg.risk_dry_run,
    )
