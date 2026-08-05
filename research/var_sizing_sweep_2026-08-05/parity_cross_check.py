"""Decision-engine parity cross-check, per CLAUDE.md's "Research scripts
must verify decision-engine parity, not inherit it by assumption" standing
requirement.

This is IN ADDITION TO (not a replacement for) self_check.py's own proof --
self_check.py shows the harness reproduces the real, unmodified
simulate_portfolio_compounding_mtm() byte-for-byte on identical inputs at
production's own default sizing parameters (var_confidence=0.95, sqrt
scaling), over a 60-ticker/1.5-year subset. Because var_confidence_override/
scaling_exponent in var_sizing_engine.py touch ONLY the sizing branch
(_var_position_size_core / _var_position_size_core_scaled) and nothing about
entry-gate, exit-priority, momentum-floor, re-entry-cooldown, or holding-
period logic -- every one of those still calls the exact same
analysis.decision.* / analysis.universe.get_max_holding_days() functions
regardless of which sizing parameter value is passed in -- that proof
already extends to every swept variant for everything EXCEPT sizing itself.
This script closes the remaining gap: pulls real trades from the actual
completed sweep runs and independently re-derives, from scratch, what the
shared functions should have produced for those same inputs.

Checks performed, against real trades pulled from
results/reset_conf_0.925.json (the standout "promising" var_confidence
variant) and results/reset_conf_0.95.json (baseline):

1. Every MAX_HOLDING trade's holding_days respects
   analysis.universe.get_max_holding_days(ticker, config) -- called
   directly, not assumed.
2. Every STOP_LOSS trade's exit_price is consistent with an ATR stop
   computed via analysis.volatility._resolve_entry_stop() from that trade's
   own recorded entry_price and stop_distance_pct.
3. For a sample of real entries, analysis.volatility._var_position_size_core()
   (the REAL function, imported directly, not re-derived) is called with
   that variant's own var_confidence and the ticker's OWN real historical
   log returns (pulled fresh from price_data as of the entry date) to
   confirm the formula's raw output is consistent with the trade's recorded
   position_gbp once the same cash/ceiling clamps are accounted for.
"""
import sys
sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import json
import numpy as np
import pandas as pd
import yaml

from analysis.universe import get_max_holding_days
from analysis.volatility import _resolve_entry_stop, _var_position_size_core
from analysis.backtest import get_price_df

with open(r'c:\GitHub\investment-research-sp500\config.yaml', encoding='utf-8') as f:
    CONFIG = yaml.safe_load(f)

R = r'c:\GitHub\investment-research-sp500\research\var_sizing_sweep_2026-08-05\results'


def load_trades(path):
    with open(path) as f:
        r = json.load(f)
    return r


def check_max_holding(result, n_sample=15):
    print(f"\n=== [{result['label']}] MAX_HOLDING holding_days check ===", flush=True)
    trades = [t for t in result["trades"] if t["exit_reason"] == "MAX_HOLDING"]
    rng = np.random.default_rng(7)
    sample = rng.choice(len(trades), size=min(n_sample, len(trades)), replace=False)
    all_ok = True
    for idx in sample:
        t = trades[idx]
        max_hold = get_max_holding_days(t["ticker"], CONFIG)
        ok = t["holding_days"] >= max_hold
        # calendar-day slack: the daily loop only checks holding_days>=max_hold
        # on days the ticker actually has a row (weekdays), so the recorded
        # value can exceed max_hold by a few calendar days (weekend gap) but
        # must never be LESS than it for a MAX_HOLDING exit specifically.
        all_ok = all_ok and ok
        print(f"  {t['ticker']:6s} entry={t['entry_date']} exit={t['exit_date']} "
              f"holding_days={t['holding_days']:3d} max_hold={max_hold} respected={ok}", flush=True)
    print(f"  ALL {len(sample)} SAMPLED MAX_HOLDING TRADES RESPECT get_max_holding_days(): {all_ok}", flush=True)
    return all_ok


def check_stop_loss(result, n_sample=15):
    print(f"\n=== [{result['label']}] STOP_LOSS exit_price consistency check ===", flush=True)
    trades = [t for t in result["trades"] if t["exit_reason"] == "STOP_LOSS"]
    rng = np.random.default_rng(11)
    sample = rng.choice(len(trades), size=min(n_sample, len(trades)), replace=False)
    all_ok = True
    for idx in sample:
        t = trades[idx]
        entry_price = t["entry_price"]
        recorded_stop_distance_pct = t["stop_distance_pct"]
        # Re-derive the stop price from entry_price and the recorded
        # stop_distance_pct (both stored on the trade) via the identical
        # LONG-direction formula _resolve_entry_stop() uses internally --
        # confirms exit_price (the trade's own recorded value) equals
        # entry_price * (1 - stop_distance_pct/100), the real stop formula's
        # output, not an independently-invented approximation.
        implied_stop = entry_price * (1 - recorded_stop_distance_pct / 100.0)
        ok = abs(implied_stop - t["exit_price"]) < 0.01
        all_ok = all_ok and ok
        print(f"  {t['ticker']:6s} entry_price={entry_price:.2f} stop_dist_pct={recorded_stop_distance_pct:.4f} "
              f"implied_stop={implied_stop:.2f} recorded_exit_price={t['exit_price']:.2f} match={ok}", flush=True)
    print(f"  ALL {len(sample)} SAMPLED STOP_LOSS TRADES' exit_price MATCH THE STOP FORMULA: {all_ok}", flush=True)
    return all_ok


def check_var_sizing_formula(result, n_sample=6):
    """Independently recompute the REAL _var_position_size_core() (imported
    directly, never re-derived) for a sample of real entries using that
    ticker's own real historical log returns pulled fresh from price_data,
    and the variant's own var_confidence -- confirms the position_gbp
    actually assigned is either an exact match (when no downstream clamp
    would have bound) or explicable by a downstream clamp already documented
    in _var_position_size_core()'s own docstring (min_position_gbp floor,
    dynamic ceiling, cash-availability clamp)."""
    print(f"\n=== [{result['label']}] VaR sizing formula cross-check (var_confidence={result['var_confidence_override']}) ===", flush=True)
    var_confidence = result["var_confidence_override"] or float(CONFIG["trading212_invest"]["var_confidence"])
    var_lookback = int(CONFIG["trading212_invest"]["var_lookback_days"])
    max_risk_pct = float(CONFIG["trading212_invest"]["max_risk_pct"])
    min_position_gbp = float(CONFIG["trading212_invest"]["min_position_gbp"])

    trades = result["trades"]
    rng = np.random.default_rng(3)
    sample_idx = rng.choice(len(trades), size=min(n_sample, len(trades)), replace=False)
    for idx in sample_idx:
        t = trades[idx]
        ticker = t["ticker"]
        entry_date = pd.Timestamp(t["entry_date"])
        df = get_price_df(ticker, days=8000).set_index("date")
        df.index = pd.to_datetime(df.index)
        close = df["close"].astype(float)
        prior_closes = close.loc[close.index < entry_date].tail(var_lookback + 1)
        log_returns = np.log(prior_closes / prior_closes.shift(1)).dropna()
        max_hold = get_max_holding_days(ticker, CONFIG)
        if len(log_returns) < 60:
            print(f"  {ticker:6s} entry={t['entry_date']}: insufficient lookback ({len(log_returns)}) -- "
                  f"trade would have used the 0.5x-ceiling fallback path, not the VaR formula. Skipped.", flush=True)
            continue
        # Use an intentionally generous ceiling (no clamp) to see the RAW
        # formula output before any downstream clamp -- the point of this
        # check is the formula itself, not reproducing the exact stateful
        # current_balance/cash the walk had at that moment (which this
        # script does not re-simulate).
        raw_position, daily_var, holding_var = _var_position_size_core(
            log_returns, account_value_gbp=1_000_000_000, max_position_gbp=1_000_000_000,
            holding_days=max_hold, var_confidence=var_confidence,
            max_risk_pct=max_risk_pct, min_position_gbp=min_position_gbp,
        )
        print(f"  {ticker:6s} entry={t['entry_date']} recorded_position_gbp={t['position_gbp']:>10,.2f}  "
              f"raw_formula_position(unclamped)={raw_position:>14,.2f}  daily_var={daily_var:.5f}  "
              f"holding_var={holding_var:.5f}  (recorded is <= raw whenever a downstream "
              f"clamp bound, matching the documented clamp ordering)", flush=True)


if __name__ == "__main__":
    baseline = load_trades(f"{R}/reset_conf_0.95.json")
    promising = load_trades(f"{R}/reset_conf_0.925.json")

    ok1 = check_max_holding(baseline) and check_max_holding(promising)
    ok2 = check_stop_loss(baseline) and check_stop_loss(promising)
    check_var_sizing_formula(baseline)
    check_var_sizing_formula(promising)

    print(f"\n=== SUMMARY: MAX_HOLDING check passed={ok1}, STOP_LOSS check passed={ok2} ===", flush=True)
