"""Empirical test of the square-root-of-time scaling ASSUMPTION itself,
independent of any backtest run -- does realized multi-day return dispersion
in this system's actual candidate universe actually scale as
sqrt(holding_days) (the current hardcoded formula), or something else?

IMPORTANT CONTEXT discovered while building this (confirmed directly, not
assumed -- see check_holding_days_is_constant.py / the REPORT.md section
"holding_days passed into sizing is not the realized per-trade holding
period"): analysis/backtest.py:simulate_portfolio_compounding_mtm() and
brokers/trading212.py both pass st["max_hold"] / get_max_holding_days(ticker,
config) into _var_position_size_core() as `holding_days` -- the ticker's
MAXIMUM ALLOWED holding period (a constant 30 for every live stock
candidate, confirmed directly: get_max_holding_days() returns exactly {30}
across all 503 live candidates, since ETFs are structurally absent from live
trading and every stock uses stock_max_holding_days=30). This is NOT the
trade's own eventual realized holding period (which cannot be known at entry
time) -- it is a fixed, conservative worst-case horizon applied identically
to every single trade. This means the practical question this scaling
exponent actually answers is narrower than "does sizing correctly
differentiate a 3-day hold from a 25-day hold" (it currently doesn't, and
no exponent choice would change that without ALSO changing what value is
passed as holding_days) -- it is "is 30 trading days the right one-size-
horizon to size against, and does dispersion at that specific horizon
actually scale the way sqrt(30) assumes." This script answers exactly that,
computing the fit across the full observed horizon range (1 to 33 calendar-
day holds, matching the real distribution -- see holding-period section of
REPORT.md) so the N=30 point specifically can be read off directly, not just
inferred from a fitted line.

Method: for each of a representative sample of live-universe stock tickers,
using that ticker's full available daily close history:
  1. Compute daily log returns r_t = ln(P_t / P_{t-1}).
  2. For each horizon N in HORIZONS below, compute the N-day ROLLING
     (overlapping) cumulative log return: R_N(t) = ln(P_t / P_{t-N}).
  3. VaR_N = the (1 - var_confidence)*100 percentile of R_N's distribution
     -- var_confidence=0.95 (current config value), i.e. the 5th percentile,
     the EXACT SAME statistic _var_position_size_core() computes for N=1
     (its `daily_var`), just generalized to N>1 real multi-day returns
     instead of daily_var*sqrt(N) as an assumed proxy for it.
  4. Per ticker, fit log(|VaR_N|) = intercept + k * log(N) by OLS across
     all N in HORIZONS (least-squares slope = the empirically-implied
     scaling exponent for that ticker). k=0.5 exactly reproduces sqrt-time
     scaling; k=1.0 reproduces linear; k=0 reproduces no scaling.
  5. Aggregate k across all tickers (mean, median, std, and the % with
     0.4 <= k <= 0.6 -- "close enough to sqrt to call it confirmed").
  6. Also report the DIRECT ratio |VaR_30| / |VaR_1| against the
     sqrt(30)=5.477 and linear(30)=30 hypotheses, since N=30 is the one
     horizon this system's sizing formula actually uses in practice.

Standard deviation (not percentile) of the same N-day rolling returns is
computed alongside as a cross-check -- if the percentile-based and std-dev-
based fits materially disagree, that's itself worth reporting (would
suggest the multi-day return distribution's SHAPE, not just its scale,
changes with horizon -- e.g. fatter/thinner tails at longer horizons).

Uses OVERLAPPING (rolling) N-day windows, a standard practitioner
simplification -- consecutive windows are autocorrelated, so this is not a
textbook independent-sample estimate, but it is the same trade-off any
realistic dispersion-vs-horizon check on ~19 years of daily data has to
make (non-overlapping 30-day blocks over even the longest available history
give only a few dozen independent samples per ticker, far too few for a
stable percentile estimate) -- reported as a methodology caveat, not hidden.
"""
import sys
sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import numpy as np
import pandas as pd
import yaml

import analysis.universe as universe
from analysis.backtest import precompute_ticker

with open(r'c:\GitHub\investment-research-sp500\config.yaml', encoding='utf-8') as f:
    CONFIG = yaml.safe_load(f)

VAR_CONFIDENCE = float(CONFIG["trading212_invest"]["var_confidence"])  # 0.95
HORIZONS = [1, 2, 3, 5, 7, 10, 15, 20, 25, 30, 33]
SAMPLE_SEED = 20260805
SAMPLE_SIZE = 120  # representative subsample of the live universe, for speed


def main():
    end = pd.Timestamp("2026-08-03")
    all_candidates = universe.get_live_stock_candidates(as_of=end.date())
    rng = np.random.default_rng(SAMPLE_SEED)
    sample = sorted(rng.choice(all_candidates, size=min(SAMPLE_SIZE, len(all_candidates)), replace=False).tolist())
    print(f"Sampling {len(sample)} of {len(all_candidates)} live candidates (seed={SAMPLE_SEED})", flush=True)

    per_ticker_fits = []
    ratio_30_over_1 = []
    skipped = 0

    for ticker in sample:
        c = precompute_ticker(ticker, CONFIG)
        if c is None:
            skipped += 1
            continue
        close = c["frame"]["close"].astype(float).dropna()
        if len(close) < max(HORIZONS) + 260:  # need real history beyond the longest horizon
            skipped += 1
            continue

        var_pct = []
        std_pct = []
        for N in HORIZONS:
            r_n = np.log(close / close.shift(N)).dropna()
            if len(r_n) < 60:
                var_pct.append(np.nan)
                std_pct.append(np.nan)
                continue
            var_pct.append(float(np.percentile(r_n, (1 - VAR_CONFIDENCE) * 100)))
            std_pct.append(float(r_n.std()))

        var_pct = np.array(var_pct)
        std_pct = np.array(std_pct)
        valid = ~np.isnan(var_pct)
        if valid.sum() < len(HORIZONS):
            skipped += 1
            continue

        logN = np.log(np.array(HORIZONS, dtype=float))
        log_abs_var = np.log(np.abs(var_pct))
        log_std = np.log(std_pct)

        # OLS slope: k = cov(logN, logY) / var(logN)
        k_var = float(np.polyfit(logN, log_abs_var, 1)[0])
        k_std = float(np.polyfit(logN, log_std, 1)[0])

        per_ticker_fits.append({"ticker": ticker, "k_var_percentile": k_var, "k_std": k_std})

        var_1 = var_pct[HORIZONS.index(1)]
        var_30 = var_pct[HORIZONS.index(30)]
        if var_1 != 0:
            ratio_30_over_1.append(abs(var_30 / var_1))

    print(f"Usable tickers: {len(per_ticker_fits)}, skipped (insufficient history): {skipped}", flush=True)

    k_vars = np.array([f["k_var_percentile"] for f in per_ticker_fits])
    k_stds = np.array([f["k_std"] for f in per_ticker_fits])
    ratios = np.array(ratio_30_over_1)

    def _summary(arr, label):
        print(f"\n{label}: n={len(arr)}", flush=True)
        print(f"  mean={np.mean(arr):.4f}  median={np.median(arr):.4f}  std={np.std(arr):.4f}", flush=True)
        print(f"  p10={np.percentile(arr,10):.4f}  p25={np.percentile(arr,25):.4f}  "
              f"p75={np.percentile(arr,75):.4f}  p90={np.percentile(arr,90):.4f}", flush=True)

    _summary(k_vars, "Fitted exponent k (percentile-based VaR_N, log|VaR_N| ~ k*log(N))")
    _summary(k_stds, "Fitted exponent k (std-dev-based, log(std_N) ~ k*log(N))")
    frac_close_to_half = float(np.mean((k_vars >= 0.4) & (k_vars <= 0.6)))
    print(f"\nFraction of tickers with percentile-based k in [0.4, 0.6] (\"close to sqrt\"): {frac_close_to_half:.3f}", flush=True)

    print(f"\nDirect |VaR_30| / |VaR_1| ratio (n={len(ratios)}):", flush=True)
    print(f"  mean={np.mean(ratios):.3f}  median={np.median(ratios):.3f}", flush=True)
    print(f"  sqrt(30) hypothesis = {30**0.5:.3f}   linear(30) hypothesis = 30.000   no-scaling hypothesis = 1.000", flush=True)
    # implied k from the median ratio directly: ratio = 30^k -> k = log(ratio)/log(30)
    implied_k_median = float(np.log(np.median(ratios)) / np.log(30))
    implied_k_mean = float(np.log(np.mean(ratios)) / np.log(30))
    print(f"  implied k from median ratio: {implied_k_median:.4f}   implied k from mean ratio: {implied_k_mean:.4f}", flush=True)

    import json
    out = {
        "var_confidence": VAR_CONFIDENCE, "horizons": HORIZONS,
        "n_sampled": len(sample), "n_usable": len(per_ticker_fits), "n_skipped": skipped,
        "k_var_percentile_mean": float(np.mean(k_vars)), "k_var_percentile_median": float(np.median(k_vars)),
        "k_var_percentile_std": float(np.std(k_vars)),
        "k_std_mean": float(np.mean(k_stds)), "k_std_median": float(np.median(k_stds)),
        "frac_k_in_0.4_0.6": frac_close_to_half,
        "ratio_30_over_1_mean": float(np.mean(ratios)), "ratio_30_over_1_median": float(np.median(ratios)),
        "implied_k_from_median_ratio": implied_k_median, "implied_k_from_mean_ratio": implied_k_mean,
        "per_ticker_fits": per_ticker_fits,
    }
    out_path = r'c:\GitHub\investment-research-sp500\research\var_sizing_sweep_2026-08-05\fitted_exponent_result.json'
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nWritten to {out_path}", flush=True)


if __name__ == "__main__":
    main()
